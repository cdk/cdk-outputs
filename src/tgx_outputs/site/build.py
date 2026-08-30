"""Turn the snapshot into the fragments MkDocs renders.

Two rules are enforced here rather than trusted to whoever writes the Markdown:

1. **A metric with no entry in metrics.csv does not render.** The definition
   and the figure ship together or neither ships.
2. **Every figure carries its caption block** -- what it counts, the source, when that
   source was last collected, and a link to the CSV behind it. A reader who wants to
   check a number should never have to ask how it was made. The caveat is still
   required of every metric in ``metrics.csv`` and validated there, but it is not
   rendered anywhere on the site.

Freshness comes from the data's own timestamps. ``datetime.now()`` is never used to
describe how current the page is.
"""

from __future__ import annotations

import json
import re
import struct
from typing import Any

from .. import config as cfg
from .. import store
from ..derive import freshness, tables
from .charts import CHARTS, PALETTE, REGISTRY_NAMES
from .flow import endpoint_patterns, source_flow
from .icons import svg as icon

INCLUDES = cfg.ROOT / "includes"   # outside docs/ so MkDocs sees them as snippets, not pages


class MissingDefinition(RuntimeError):
    pass


MISSING = "not collected"


def _value(snapshot: dict[str, Any], metric: str, entity: str | None = None) -> float | None:
    """Sum a metric, or None if it was not collected.

    The distinction is the whole point. When a source fails, summing its (absent)
    records yields 0.0, and a tile then states "0 pathways" with total confidence.
    A missing number must read as missing.
    """
    recs = tables.series(snapshot, metric)
    if entity:
        recs = [r for r in recs if r["entity"] == entity]
    if not recs:
        return None
    return sum(r["value"] for r in recs)


def _fmt(value: float) -> str:
    if value >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.0f}" if value == int(value) else f"{value:,.1f}"


def figure(name: str, snapshot: dict[str, Any], fresh: dict[str, Any]) -> str:
    """One chart plus the caption block that makes it checkable."""
    if name not in CHARTS:
        raise MissingDefinition(f"no chart registered as {name!r}")
    builder, metric = CHARTS[name]
    semantics = cfg.semantics()
    if metric not in semantics:
        raise MissingDefinition(
            f"chart {name!r} renders metric {metric!r}, which has no entry in "
            "config/metrics.csv -- define what it counts before showing it")
    spec = semantics[metric]
    source = spec["source"]
    row = next((r for r in fresh["sources"] if r["source"] == source), None)
    collected = row["fetched_at"][:10] if row else "unknown"
    level = row["level"] if row else "red"
    # Stale and incomplete are different complaints and get different words. A figure
    # drawn from numbers gathered minutes ago but missing one row is not stale, and
    # calling it that sends the reader looking for a collection problem there isn't one.
    badge = {"fresh": "", "amber": " ⚠ stale", "red": " ⛔ stale"}[level]
    if row and not row["complete"]:
        badge += " ⚠ incomplete"

    # The spec goes in a child <script type="application/json">, not an attribute.
    # Vega-Lite filter expressions contain single quotes (datum.entity == 'all'), which
    # silently truncate a single-quoted HTML attribute and leave a chart that renders
    # nothing. Only the literal string "</script" needs escaping here.
    if not tables.series(snapshot, metric):
        return (
            f'<div class="tgx-caption" markdown>\n'
            f'**{spec["label"]} — {MISSING}.** The `{source}` source did not return data '
            f'on the last run ({collected}), so this figure is not shown rather than '
            f'drawn from stale or partial values. '
            f'See [collection status](methods.md#collection-status).\n'
            f'</div>\n')

    spec_json = json.dumps(builder(), separators=(",", ":")).replace("</", "<\\/")
    # Vega-Lite renders an SVG with no accessible name, so a screen reader announces
    # seven unlabelled graphics. The caption below already says what the chart is;
    # naming the container says the same thing to a reader who cannot see it.
    label = f'{spec["label"]}. {spec["counts"]}'.replace('"', "&quot;")
    return (
        f'<div class="tgx-chart" role="img" aria-label="{label}">'
        f'<script type="application/json" class="tgx-spec">{spec_json}</script>'
        f'</div>\n\n'
        f'<div class="tgx-caption" markdown>\n'
        f'**{spec["label"]}.** {spec["counts"]}\n\n'
        f'<small>Source: `{source}` · collected {collected}{badge} · '
        f'[download CSV](data/{metric}.csv)</small>\n'
        f'</div>\n'
    )


def _freshness_strip(fresh: dict[str, Any], *, table: bool) -> str:
    """How current the page is, in one line, and optionally the per-source detail.

    Both pages want the line -- it is the first thing that tells a reader whether to
    trust what follows. Only Methods wants the table under it: the Overview carried a
    copy that said the same thing a page away, and a fact stated twice is a fact that
    can end up disagreeing with itself.
    """
    css = {"fresh": "tgx-fresh", "amber": "tgx-amber", "red": "tgx-red"}[fresh["level"]]
    strip = f'<div class="tgx-strip {css}">{fresh["summary"]}</div>\n'
    if not table:
        return (strip + '\n<small>Per-source detail is on the '
                '[Methods page](methods.md#collection-status).</small>\n')
    def got(r: dict[str, Any]) -> str:
        if r["expected"] and r["found"] is not None:
            return f"{r['found']} of {r['expected']} {r['unit']}"
        return "—" if r["complete"] else "incomplete"

    rows = "\n".join(
        f"| `{r['source']}` | {r['status']} | {r['fetched_at'][:10]} | "
        f"{r['age_days']:.0f} d | {r['record_count']:,} | {got(r)} |"
        for r in fresh["sources"])
    return (
        strip + "\n"
        '| Source | Status | Last collected | Age | Records | Completeness |\n'
        '|---|---|---|---|---|---|\n' + rows + "\n"
    )


def _cards(snapshot: dict[str, Any]) -> str:
    """Headline totals, one card per source.

    Registries stay separate. Bioconductor, PyPI and npm each count something different
    over a different window, so a single "downloads" card would be a large number that
    means nothing.
    """
    recs = [r for src in snapshot.get("sources", {}).values()
            for r in src.get("records", [])]

    def total(metric: str, prefix: str | None = None) -> float | None:
        hit = [r for r in recs if r["metric"] == metric
               and (prefix is None or r["entity"].startswith(prefix))]
        return sum(r["value"] for r in hit) if hit else None

    year = str(int(snapshot.get("collected_on", "2026")[:4]) - 1)
    releases = sum(r["value"] for r in recs
                   if r["metric"] == "releases_by_year" and r.get("period", "") >= year) or None

    # The registry marks say where a number comes from faster than the label does; the
    # rest are generic glyphs, because there is no logo for "a release" or "a service".
    cards = [
        ("Projects tracked", float(len(cfg.projects())), "#", "", "repo"),
        ("Releases", releases, "#releases", f"since {year}", "tag"),
        ("Bioconductor", total("package_downloads_total", "bioconductor.org/"),
         "#downloads", "downloads, all time", "bioconductor"),
        # npm and PyPI publish no lifetime counter, so these are 30-day windows and the
        # sub-label has to say so. Reading one as "all time" is how this went wrong.
        ("PyPI", total("package_downloads_recent", "pypi.org/"), "#downloads",
         "downloads, last 30 days", "python"),
        ("npm", total("package_downloads_recent", "npmjs.org/"), "#downloads",
         "downloads, last 30 days", "npm"),
        # Maven Central counts no downloads, so the card that would sit beside PyPI and
        # npm carries the figure it does publish. The sub-label has to work hard here:
        # this number sits in a row of download counts and is a tenth of their size
        # while meaning something entirely different.
        ("Maven", total("package_dependents", "repo1.maven.org/"), "#dependents",
         "dependent packages", "package"),
        ("Docker Hub", total("docker_pulls_total"), "#containers", "pulls, all time",
         "docker"),
        ("Citations", total("paper_citations"), "#citations", "of these tools' papers",
         "book"),
    ]

    out = ['<div class="tgx-cards">']
    for label, value, anchor, sub, glyph in cards:
        shown = MISSING if value is None else _fmt(value)
        out.append(
            f'<a class="tgx-card" href="{anchor}">'
            f'<span class="tgx-card-label">{icon(glyph)}{label}</span>'
            f'<span class="tgx-card-value">{shown}</span>'
            f'<span class="tgx-card-sub">{sub}</span></a>')
    out.append("</div>")
    return "\n".join(out) + "\n"


LOGO_DIR = cfg.DOCS_DIR / "assets" / "images" / "logos"

# Above this, a mark is treated as a wordmark that already says the project's name and
# is allowed to stand alone in the heading; below it, as an icon that needs the name
# beside it. Measured rather than configured, so replacing a file changes the layout
# with it. The gap between the two groups is wide -- the wordmarks here run from 3.2:1
# to 6.8:1 and the icons from 1.0:1 to 2.0:1 -- so the exact threshold is not delicate.
WORDMARK_ASPECT = 2.5


def _logo_aspect(filename: str) -> float:
    """Width over height of a logo file, or 1.0 if it cannot be read.

    Parsed here rather than with an image library: the whole build depends on nothing
    but the standard library plus what the collectors need, and a PNG header and an SVG
    viewBox are a few lines each. An unreadable file falls back to the icon treatment,
    which shows the project's name too and so is the safer of the two to be wrong about.
    """
    path = LOGO_DIR / filename
    try:
        raw = path.read_bytes()
    except OSError:
        return 1.0
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        # IHDR is always the first chunk: width and height are big-endian at byte 16.
        width, height = struct.unpack(">II", raw[16:24])
        return width / height if height else 1.0
    if filename.endswith(".svg"):
        head = raw[:4000].decode("utf-8", "replace")
        box = re.search(r'viewBox\s*=\s*["\']\s*[-\d.]+[ ,]+[-\d.]+[ ,]+'
                        r'([\d.]+)[ ,]+([\d.]+)', head)
        if box:
            width, height = float(box.group(1)), float(box.group(2))
            return width / height if height else 1.0
    return 1.0


def _mark(proj: dict[str, Any]) -> str:
    """The letters on a tile when the project has no logo file.

    Taken from `mark:` in the config tables where a project sets one, because initials
    derived from a name are wrong often enough to be worth overriding: molAOP is not
    "MB" and R-ODAF is not "RS". The derivation is the fallback, so a project added
    without the field still gets a tile rather than a blank square.
    """
    if proj.get("mark"):
        return str(proj["mark"])[:3]
    words = [w for w in re.split(r"[^0-9A-Za-z]+", proj["name"]) if w]
    caps = "".join(c for w in words for c in w if c.isupper())
    if len(caps) >= 2:
        return caps[:3]
    return (words[0][:2] if words else proj["id"][:2]).upper()


def _stat(value: str, label: str) -> str:
    return (f'<div class="tgx-stat"><span class="tgx-stat-value">{value}</span>'
            f'<span class="tgx-stat-label">{label}</span></div>')


# Where a tracked identifier lives, so the tile can link it without anyone writing the
# URL down twice. The identifiers are already in config/identifiers.csv for collection;
# these turn each one into the page a reader would want. A registry missing from here
# is simply not linked, which is visible on the page rather than silently wrong.
REGISTRY_URLS = {
    "bioconductor.org": ("Bioconductor", "https://bioconductor.org/packages/{name}"),
    "pypi.org": ("PyPI", "https://pypi.org/project/{name}/"),
    "npmjs.org": ("npm", "https://www.npmjs.com/package/{name}"),
    "cran.r-project.org": ("CRAN", "https://cran.r-project.org/package={name}"),
    # Maven Central's own browse UI is central.sonatype.com, and it wants the group and
    # the artifact as path segments rather than the colon-joined coordinate.
    "repo1.maven.org": ("Maven Central",
                        "https://central.sonatype.com/artifact/{group}/{artifact}"),
    "conda-forge.org": ("conda-forge", "https://anaconda.org/conda-forge/{name}"),
    "debian-13": ("Debian 13 (Trixie)", "https://packages.debian.org/search?suite=trixie&keywords={name}"),
    "ubuntu-24.04": ("Ubuntu 24.04", "https://packages.ubuntu.com/search?suite=noble&keywords={name}"),
}


def _registry_links(proj: dict[str, Any]) -> list[str]:
    """Every place a project is published, built from the identifiers it declares.

    Not a hand-maintained list: the same rows that tell the collectors where to look
    tell the page where to link, so the two cannot drift apart and adding a package
    adds its link for free.
    """
    out: list[str] = []
    for ref in proj.get("packages") or []:
        registry, name = ref.split("/", 1)
        entry = REGISTRY_URLS.get(registry)
        if entry is None:
            continue
        label, template = entry
        group, _, artifact = name.partition(":")
        url = template.format(name=name, group=group, artifact=artifact or name)
        out.append(f'<a href="{url}">{label}</a> <span class="tgx-drop-meta">{name}</span>')
    for image in proj.get("docker") or []:
        out.append(f'<a href="https://hub.docker.com/r/{image}">Docker Hub</a> '
                   f'<span class="tgx-drop-meta">{image}</span>')
    for slug in proj.get("rsd") or []:
        out.append(f'<a href="https://research-software-directory.org/software/{slug}">'
                   f'Research Software Directory</a> '
                   f'<span class="tgx-drop-meta">{slug}</span>')
    for repo in proj.get("repos") or []:
        out.append(f'<a href="https://github.com/{repo}">GitHub</a> '
                   f'<span class="tgx-drop-meta">{repo}</span>')
    return out


def _drop(kind: str, entries: list) -> str:
    """One collapsed list on a tile: its links, or the papers behind its citations.

    ``<details>`` rather than a scripted disclosure, so it works with JavaScript off
    and the browser handles the keyboard for us. The summary carries the count, which
    is the part worth reading without opening anything.
    """
    if not entries:
        return ""
    plural = "" if len(entries) == 1 else ("ies" if kind.endswith("y") else "s")
    kind = kind[:-1] if (plural == "ies") else kind
    if kind != "paper":
        items = "".join(f"<li>{html}</li>" for html in entries)
    else:
        items = ""
        for paper in entries:
            tag = ('<span class="tgx-tag">preprint</span>'
                   if paper["type"] == "preprint" else "")
            year = f'{paper["year"]} · ' if paper["year"] else ""
            cites = f'{_fmt(paper["citations"])} citation'
            cites += "" if paper["citations"] == 1 else "s"
            items += (f'<li><a href="https://doi.org/{paper["doi"]}">{paper["title"]}</a>'
                      f'{tag}<span class="tgx-drop-meta">{year}{cites}</span></li>')
    return (f'<details class="tgx-drop">'
            f'<summary><span class="tgx-caret"></span>{len(entries)} {kind}{plural}'
            f'</summary>'
            f'<ul class="tgx-drop-list">{items}</ul></details>')


def _project_tiles(snapshot: dict[str, Any]) -> str:
    """One tile per tracked project. The whole point of the page.

    This was a six-column table until 2026-08-25. A table makes ten projects look
    like ten rows of one thing and invites the reading it was built to prevent --
    scanning down a column and ranking the department's tools against each other,
    when the columns hold different registries' measures over different windows.
    A tile shows each project with its own numbers, and there is no column to run
    your eye down.

    Nothing new is collected here. Every figure on a tile was already in the table.
    """
    recs = [r for src in snapshot.get("sources", {}).values()
            for r in src.get("records", [])]

    def by(metric: str) -> list[dict[str, Any]]:
        return [r for r in recs if r["metric"] == metric]

    latest = {r["entity"]: (r.get("extra") or {}).get("date", "") for r in by("latest_release")}
    # Per registry, never summed. Bioconductor's figure is a lifetime total and npm's
    # is a rolling 30 days; one number holding their sum means nothing.
    downloads: dict[str, list[tuple[str, float, str]]] = {}
    for metric, window in (("package_downloads_total", "all time"),
                           ("package_downloads_recent", "last 30 days")):
        for r in by(metric):
            pid = (r.get("extra") or {}).get("project")
            if pid:
                registry = REGISTRY_NAMES.get(
                    (r.get("extra") or {}).get("registry", ""), r["entity"].split("/")[0])
                downloads.setdefault(pid, []).append((registry, r["value"], window))
    pulls: dict[str, float] = {}
    for r in by("docker_pulls_total"):
        pid = (r.get("extra") or {}).get("project")
        if pid:
            pulls[pid] = pulls.get(pid, 0) + r["value"]
    # One entry per DOI rather than the summed count, so a tile can list the papers
    # it is counting instead of only saying how many there were.
    papers_of: dict[str, list[dict[str, Any]]] = {}
    for r in by("paper_citations_by_doi"):
        extra = r.get("extra") or {}
        pid = extra.get("project")
        if pid:
            papers_of.setdefault(pid, []).append(
                {"doi": r["entity"], "citations": r["value"],
                 # Applied here rather than in the collector so a correction reaches
                 # the page on the next build, without waiting for a re-collect.
                 "title": cfg.corrected(
                     "paper", r["entity"], "title",
                     extra.get("title") or r["entity"]),
                 "year": extra.get("year"), "type": extra.get("type")})
    for entries in papers_of.values():
        # Newest first: the current paper is the one to cite.
        entries.sort(key=lambda e: (e["year"] or 0), reverse=True)

    cites = {r["entity"]: r["value"] for r in by("paper_citations")}
    papers = {r["entity"]: (r.get("extra") or {}).get("papers", 0)
              for r in by("paper_citations")}

    cutoff = str(int(snapshot.get("collected_on", "2026")[:4]) - 1)
    recent: dict[str, float] = {}
    for r in by("releases_by_year"):
        if r.get("period", "") >= cutoff:
            recent[r["entity"]] = recent.get(r["entity"], 0) + r["value"]

    out = ['<div class="tgx-projects">']
    for i, proj in enumerate(cfg.projects()):
        pid = proj["id"]
        accent = PALETTE[i % len(PALETTE)]

        stats = []
        if pid in cites:
            n = papers.get(pid) or 0
            stats.append(_stat(_fmt(cites[pid]),
                               f"citations · {n} paper{'s' if n != 1 else ''}"))
        for registry, value, window in sorted(
                downloads.get(pid, []), key=lambda e: (e[2] != "all time", -e[1])):
            stats.append(_stat(_fmt(value), f"{registry} downloads · {window}"))
        if pulls.get(pid):
            stats.append(_stat(_fmt(pulls[pid]), "Docker Hub pulls · all time"))
        if pid in recent:
            stats.append(_stat(_fmt(recent[pid]), f"releases since {cutoff}"))
        if latest.get(pid):
            stats.append(_stat(latest[pid], "last release"))

        # Three ways to head a tile, and which one depends on the mark the project
        # publishes. A wordmark says the name already, so it stands alone in the
        # heading with the name as its alt text -- printing both says it twice. A
        # square icon says nothing a stranger can read at this size, so it sits
        # beside the name exactly as a monogram would. A project with no mark gets
        # the monogram. All three keep an h3, so the page still has one heading per
        # project for anyone navigating by them.
        logo = proj.get("logo")
        name = f'<h3 class="tgx-project-name">{proj["name"]}</h3>'
        if logo and _logo_aspect(logo) >= WORDMARK_ASPECT:
            brand = (f'<h3 class="tgx-project-name tgx-project-branded">'
                     f'<img class="tgx-project-logo" '
                     f'src="assets/images/logos/{logo}" alt="{proj["name"]}">'
                     f'</h3>')
        elif logo:
            brand = (f'<img class="tgx-project-icon" '
                     f'src="assets/images/logos/{logo}" alt="">{name}')
        else:
            brand = (f'<span class="tgx-project-mark" aria-hidden="true">'
                     f'{_mark(proj)}</span>{name}')

        # `links:` first and in the order the tables write them, then the services.
        # Which link leads is an editorial choice about the project -- for BridgeDb and
        # WikiPathways the project's own site is what a stranger wants first, not the
        # machine endpoint -- and the YAML file is where that choice belongs, not here.
        # Deduplicated on the URL because a service and a link often name the same page.
        links, seen = [], set()
        entries = [(label, url) for label, url in (proj.get("links") or {}).items()]
        entries += [(svc["name"], svc["url"]) for svc in proj.get("services") or []]
        for label, url in entries:
            if url in seen:
                continue
            seen.add(url)
            links.append(f'<a href="{url}">{label}</a>')

        # Where to go and what to cite, folded away. Eleven tiles each listing four
        # links and up to seven papers is a wall of blue text that buries the numbers
        # the tile exists to show; behind a toggle the same material is one click away
        # and costs a line. Closed by default, and a <details> needs no JavaScript, so
        # it still opens on a page with scripting turned off.
        drops = (_drop("link", links)
                 + _drop("registry", _registry_links(proj))
                 + _drop("paper", papers_of.get(pid, [])))

        out.append(
            f'<article class="tgx-project" style="--tgx-project-accent: {accent}">'
            f'<div class="tgx-project-brand">{brand}</div>'
            f'<p class="tgx-project-what">{proj["what"].strip()}</p>'
            + (f'<div class="tgx-project-stats">{"".join(stats)}</div>'
               if stats else
               '<div class="tgx-project-stats tgx-project-quiet">'
               '<div class="tgx-stat"><span class="tgx-stat-label">'
               'nothing measurable is published for this one yet</span></div></div>')
            + (f'<div class="tgx-project-foot">{drops}</div>' if drops else "")
            + '</article>')
    out.append("</div>")

    # The caveats that used to sit here as four paragraphs are on each metric's entry
    # in the Methods catalogue, and under each figure's own toggle. Repeating them at
    # the foot of the grid put the longest block of text on the page under the part a
    # reader had already understood.
    return "\n".join(out) + "\n"


def _latest_manifest() -> dict[str, Any]:
    files = sorted(store.manifest_dir().glob("*.json"))
    return json.loads(files[-1].read_text()) if files else {"sources": {}}


def _sources(semantics: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """Everything about one source, in one place.

    This was three sections a page apart: a catalogue saying what each source produced,
    an attribution table saying what its terms were, and a calls section drawing what
    it asked for. A reader wanting to judge one number had to hold three lists in their
    head and join them by source name. Grouped, each source is a single section -- what
    it is, what this project takes from it, the shape of what it asks, and every
    request from the last run.

    Written from the run manifest and the config tables, so it cannot describe a call
    the pipeline no longer makes or a metric nobody defined.
    """
    manifest = _latest_manifest()
    collectors = cfg.sources()["collectors"]

    by_source: dict[str, list] = {}
    for name, spec in sorted(semantics.items()):
        by_source.setdefault(spec["source"], []).append((name, spec))

    out: list[str] = []
    for name in sorted(collectors):
        spec = collectors[name]
        metrics = by_source.get(name, [])
        src = manifest.get("sources", {}).get(name, {})
        snap_src = snapshot.get("sources", {}).get(name, {})
        # A collector that is switched off has nothing to draw and asked for nothing.
        # That it exists and is off is a fact about the configuration, which the
        # collection status table above already states.
        if not spec["enabled"] and not metrics:
            continue

        out.append(f"### {spec['title']} {{ #{name} }}\n")
        out.append(f"`{name}` · [{spec['url']}]({spec['url']}) · "
                   f"terms: {spec['terms']}\n")

        if metrics:
            out.append("| Publishes | | |")
            out.append("|---|---|---|")
            for metric, m in metrics:
                kind = "level" if m["cumulative"] else f"per {m['granularity']}"
                out.append(f"| **{m['label']}** | `{metric}`, {kind} | {m['counts']} |")
            out.append("")

        calls = src.get("calls") or []
        if not calls:
            out.append("*Made no requests on the last run.*\n")
            continue

        counts: dict[str, int] = {}
        for rec in snap_src.get("records", []):
            counts[rec["metric"]] = counts.get(rec["metric"], 0) + 1
        when = (src.get("fetched_at") or "")[:10]
        out.append(
            f"{len(calls)} request{'s' if len(calls) != 1 else ''} on {when}, "
            f"status `{src.get('status', 'unknown')}`, "
            f"{src.get('record_count', 0):,} records kept.\n")
        out.append(source_flow(
            name, endpoint_patterns([c["url"] for c in calls]),
            sorted(counts.items()), src.get("record_count", 0)) + "\n")

        out.append('??? note "Every request, in the order it was made"\n')
        out.append("    | # | URL | What came back |")
        out.append("    |---|---|---|")
        for i, call in enumerate(calls, 1):
            url = call["url"].replace("|", "%7C")
            mark = "" if call.get("ok") else " ⛔"
            out.append(f"    | {i} | `{url}` | {call.get('note') or ''}{mark} |")
        out.append("")

        for err in src.get("errors", [])[:3]:
            out.append(f'!!! warning "Reported a problem"\n\n    {err.splitlines()[0]}\n')
        quarantined = src.get("quarantined") or []
        if quarantined:
            rules = {q["rule"] for q in quarantined}
            out.append(
                f"{len(quarantined)} record(s) were quarantined by "
                f"{', '.join(f'`{r}`' for r in sorted(rules))} and are in the run "
                f"manifest rather than on the page.\n")

    return "\n".join(out) + "\n"


def build() -> int:
    snapshot = store.load_latest()
    semantics = cfg.semantics()
    INCLUDES.mkdir(parents=True, exist_ok=True)

    fresh = freshness.assess(snapshot)

    fragments = {
        "freshness.md": _freshness_strip(fresh, table=True),
        "freshness_brief.md": _freshness_strip(fresh, table=False),
        "cards.md": _cards(snapshot),
        "projects.md": _project_tiles(snapshot),
        "sources.md": _sources(semantics, snapshot),
    }
    for name in CHARTS:
        fragments[f"fig_{name}.md"] = figure(name, snapshot, fresh)

    for name, text in fragments.items():
        (INCLUDES / name).write_text(text)

    print(f"  {len(fragments)} fragments → {INCLUDES.relative_to(cfg.ROOT)}")
    print(f"  freshness level: {fresh['level']}")
    if fresh["level"] == "red":
        print("  ⛔ at least one source is badly stale; the page will say so")
    return 0
