"""Loading and validating the editable surface.

Everything a person edits lives in ``config/`` as a CSV table, one row per thing, so
it opens in a spreadsheet and a change shows up in review as a one-line diff. Adding a
project is a row in ``projects.csv`` plus its rows in the other tables; no Python is
involved, and a test holds that promise.

The tables are normalised rather than one wide sheet, because most of what a project
has is a list -- several repositories, several papers, several links -- and a wide
sheet holds those only as delimiter-separated cells that nothing can validate:

    projects.csv     one row per project: id, name, what, mark, logo
    identifiers.csv  project, kind, value, note -- repos, packages, docker, rsd, papers
    services.csv     project, name, url, what
    links.csv        project, label, url  (order preserved: the first is shown first)
    metrics.csv      one row per published metric, and what it counts
    collectors.csv   which sources run, how they are credited, and how often
    settings.csv     the few single values that are not a list of anything
    exclusions.csv   kind, value, reason -- what is left out, and why
    corrections.csv  kind, value, field, to, reason -- where upstream metadata is wrong

``projects()`` reassembles those into the same nested shape the collectors and the site
builder already expect, so the tables are the storage format and nothing downstream
knows the difference.

Validation lives in ``validate()`` rather than in a JSON schema: the schemas checked
YAML files that no longer exist, and a CSV needs different checks anyway -- that the
columns are the ones expected, that every foreign key names a real project, and that
the enumerated columns hold values the code knows.
"""

from __future__ import annotations

import csv
import functools
import hashlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

# The identifier kinds, and the field each becomes on a project. Adding a kind here is
# all it takes for a new column of identifiers to reach a collector.
KINDS = {
    "repo": "repos",
    "package": "packages",
    "docker": "docker",
    "rsd": "rsd",
    "paper": "papers",
}

REGISTRIES = {"bioconductor.org", "pypi.org", "npmjs.org", "cran.r-project.org",
              "repo1.maven.org", "conda-forge.org", "debian-13", "ubuntu-24.04"}

COLUMNS = {
    "projects.csv": ["id", "name", "what", "mark", "logo"],
    "identifiers.csv": ["project", "kind", "value", "note"],
    "services.csv": ["project", "name", "url", "what"],
    "links.csv": ["project", "label", "url"],
    "metrics.csv": ["metric", "label", "counts", "source", "cumulative",
                    "granularity", "caveat"],
    "collectors.csv": ["collector", "title", "url", "terms", "enabled",
                       "cadence_days", "note"],
    "settings.csv": ["key", "value"],
    "exclusions.csv": ["kind", "value", "reason"],
    "corrections.csv": ["kind", "value", "field", "to", "reason"],
}


class ConfigError(RuntimeError):
    """A table is missing, misspelled, or points at something that is not there."""


def _read(name: str) -> list[dict[str, str]]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise ConfigError(f"missing config table: {path}")
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        expected = COLUMNS[name]
        if reader.fieldnames != expected:
            raise ConfigError(
                f"{name} has columns {reader.fieldnames}, expected {expected}")
        # Blank rows are what a spreadsheet leaves behind; they are not data.
        return [{k: (v or "").strip() for k, v in row.items()}
                for row in reader if any((v or "").strip() for v in row.values())]


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"true", "yes", "1"}


@functools.lru_cache(maxsize=1)
def projects() -> list[dict[str, Any]]:
    """The tracked projects, in the order projects.csv lists them.

    Rebuilt from four tables into the nested shape the rest of the code reads, so a
    collector still asks for ``proj["repos"]`` and never learns where that came from.
    """
    rows = _read("projects.csv")
    by_id: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        proj: dict[str, Any] = {"id": row["id"], "name": row["name"], "what": row["what"]}
        for optional in ("mark", "logo"):
            if row[optional]:
                proj[optional] = row[optional]
        by_id[row["id"]] = proj
        out.append(proj)

    for row in _read("identifiers.csv"):
        proj = by_id.get(row["project"])
        if proj is None:
            raise ConfigError(
                f"identifiers.csv names project {row['project']!r}, which is not in "
                "projects.csv")
        field = KINDS.get(row["kind"])
        if field is None:
            raise ConfigError(
                f"identifiers.csv has kind {row['kind']!r}; known kinds are "
                f"{sorted(KINDS)}")
        proj.setdefault(field, []).append(row["value"])

    for row in _read("services.csv"):
        proj = by_id.get(row["project"])
        if proj is None:
            raise ConfigError(f"services.csv names unknown project {row['project']!r}")
        proj.setdefault("services", []).append(
            {"name": row["name"], "url": row["url"], "what": row["what"]})

    for row in _read("links.csv"):
        proj = by_id.get(row["project"])
        if proj is None:
            raise ConfigError(f"links.csv names unknown project {row['project']!r}")
        # A dict, because that is what the site builder reads -- and since Python 3.7
        # it keeps insertion order, so the row order in the file is the order shown.
        proj.setdefault("links", {})[row["label"]] = row["url"]

    return out


@functools.lru_cache(maxsize=1)
def semantics() -> dict[str, Any]:
    return {
        row["metric"]: {
            "label": row["label"],
            "counts": row["counts"],
            "source": row["source"],
            "cumulative": _truthy(row["cumulative"]),
            "granularity": row["granularity"],
            "caveat": row["caveat"],
        }
        for row in _read("metrics.csv")
    }


@functools.lru_cache(maxsize=1)
def sources() -> dict[str, Any]:
    """Collector registry plus the handful of single settings.

    Shaped like the mapping the code already reads so that switching the storage
    format did not become a rewrite of everything that consumes it.
    """
    settings = {row["key"]: row["value"] for row in _read("settings.csv")}
    return {
        "collectors": {
            row["collector"]: {"enabled": _truthy(row["enabled"]),
                               "cadence_days": int(row["cadence_days"]),
                               "note": row["note"],
                               # How the source is named and credited on the page.
                               "title": row["title"],
                               "url": row["url"],
                               "terms": row["terms"]}
            for row in _read("collectors.csv")
        },
        "meta": {k: v for k, v in settings.items() if not k.startswith("rsd_")},
        "rsd": {k[len("rsd_"):]: v for k, v in settings.items() if k.startswith("rsd_")},
    }


@functools.lru_cache(maxsize=1)
def exclusions() -> dict[str, Any]:
    out: dict[str, list[dict[str, str]]] = {}
    for row in _read("exclusions.csv"):
        out.setdefault(row["kind"], []).append(
            {"value": row["value"], "reason": row["reason"]})
    return out


CORRECTABLE = {"paper": {"title"}}


@functools.lru_cache(maxsize=1)
def corrections() -> dict[tuple[str, str, str], str]:
    """Upstream metadata this project overrides, keyed by (kind, value, field).

    A registry is occasionally wrong in a way no re-fetch can repair -- a publisher
    that put a literal ``?`` where an em dash belongs, which every downstream index
    then carries verbatim. Overriding it silently would be indistinguishable from a
    bug, so a correction is a row with a required reason and the table is public.
    """
    out: dict[tuple[str, str, str], str] = {}
    for row in _read("corrections.csv"):
        key = (row["kind"], row["value"].lower(), row["field"])
        out[key] = row["to"]
    return out


def corrected(kind: str, value: str, field: str, upstream: str) -> str:
    """``upstream`` unless a correction row replaces it."""
    return corrections().get((kind, (value or "").lower(), field), upstream)


def project_ids() -> list[str]:
    return [p["id"] for p in projects()]


def project_field(field: str) -> list[tuple[str, str]]:
    """Flatten one field across projects as (project_id, value) pairs.

    Collectors iterate this rather than a per-source list, which is what keeps the
    tables the single place a person edits.
    """
    out: list[tuple[str, str]] = []
    for proj in projects():
        for value in proj.get(field) or []:
            out.append((proj["id"], value))
    return out


def config_sha() -> str:
    """Fingerprint of every config table, stamped into each snapshot."""
    h = hashlib.sha256()
    for path in sorted(CONFIG_DIR.glob("*.csv")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def collector_enabled(name: str) -> bool:
    return bool(sources().get("collectors", {}).get(name, {}).get("enabled", False))


def cadence_days(name: str) -> int:
    return int(sources().get("collectors", {}).get(name, {}).get("cadence_days", 7))


def excluded_repos() -> set[str]:
    return {e["value"] for e in (exclusions().get("repos") or [])}


def excluded_dois() -> set[str]:
    return {e["value"].lower() for e in (exclusions().get("dois") or [])}


def excluded_packages() -> set[str]:
    return {e["value"] for e in (exclusions().get("packages") or [])}


def validate() -> list[str]:
    """Everything a JSON schema used to check, plus what it could not.

    Returns the problems rather than raising, so `tgx doctor` can print all of them at
    once instead of one per run.
    """
    problems: list[str] = []

    ids = project_ids()
    if len(ids) != len(set(ids)):
        problems.append("duplicate project id in projects.csv")
    for pid in ids:
        if pid != pid.lower() or " " in pid:
            problems.append(f"project id {pid!r} should be lowercase with no spaces")
    for proj in projects():
        for required in ("id", "name", "what"):
            if not proj.get(required):
                problems.append(f"{proj.get('id', '?')}: {required} is empty")
        if proj.get("what") and not proj["what"].endswith("."):
            problems.append(f"{proj['id']}: `what` should be a full sentence")
        if proj.get("logo") and not (
                DOCS_DIR / "assets" / "images" / "logos" / proj["logo"]).exists():
            problems.append(f"{proj['id']}: logo {proj['logo']!r} is not in "
                            "docs/assets/images/logos/")

    for project, ref in project_field("packages"):
        if "/" not in ref or ref.split("/", 1)[0] not in REGISTRIES:
            problems.append(f"{project}: package {ref!r} does not name a known registry")
    for project, repo in project_field("repos"):
        if repo.count("/") != 1:
            problems.append(f"{project}: repo {repo!r} should be owner/name")

    defined = semantics()
    for name, spec in defined.items():
        for required in ("label", "counts", "source", "granularity", "caveat"):
            if not spec.get(required):
                problems.append(f"metric {name}: {required} is empty")
        if spec["cumulative"] and spec["granularity"] != "none":
            problems.append(
                f"metric {name} is cumulative but declares granularity "
                f"{spec['granularity']!r}; a level does not belong to a period")

    known_sources = set(sources()["collectors"])
    for name, spec in defined.items():
        if spec["source"] not in known_sources:
            problems.append(
                f"metric {name} comes from source {spec['source']!r}, which is not in "
                "collectors.csv")

    for kind, entries in exclusions().items():
        for entry in entries:
            if not entry.get("reason"):
                problems.append(f"exclusion {kind}/{entry['value']} has no reason")

    known_dois = {d.lower() for _, d in project_field("papers")}
    for row in _read("corrections.csv"):
        where = f"correction {row['kind']}/{row['value']}/{row['field']}"
        if not row["reason"]:
            problems.append(f"{where} has no reason")
        if not row["to"]:
            problems.append(f"{where} has nothing to correct it to")
        fields = CORRECTABLE.get(row["kind"])
        if fields is None:
            problems.append(
                f"{where} has kind {row['kind']!r}; correctable kinds are "
                f"{sorted(CORRECTABLE)}")
        elif row["field"] not in fields:
            problems.append(
                f"{where} corrects field {row['field']!r}; {row['kind']} allows "
                f"{sorted(fields)}")
        elif row["kind"] == "paper" and row["value"].lower() not in known_dois:
            # A correction for a DOI nothing tracks is dead weight that will outlive
            # whoever wrote it.
            problems.append(f"{where} names a DOI that is not in identifiers.csv")

    return problems
