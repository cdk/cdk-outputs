"""Vega-Lite specs.

Specs are written as plain dicts and point at the CSV the page also offers for
download, so the chart and the download link cannot disagree: they are the same file.

Colours come from a small palette that keeps its contrast in both light and dark mode,
and every spec sets an explicit background of ``transparent`` so it inherits the page
rather than punching a white rectangle into a dark theme.
"""

from __future__ import annotations

from typing import Any

# For a series that genuinely needs telling apart -- the packages on the Bioconductor
# line chart, the monogram on each project tile. The first two are the department's own
# logo colours, so the page is recognisably from the same family as the mark in the
# header; the rest extend the range. All six read acceptably in light and dark and stay
# distinguishable in greyscale print.
#
# A bar chart here is NOT one of those cases. Every bar chart on this page has one bar
# per package or per project, so colouring by that field encodes nothing the y-axis
# label does not already say, and six rotating colours read as a category that is not
# there. Bars take the single house colour instead: UM Blue on white, the logo's blue
# on the slate ground. That pair lives in ``themed()`` in charts.js and only there,
# because the theme is a fact the browser knows and this build does not.
PALETTE = ["#00a2db", "#e84e10", "#4a8a72", "#8a5fa8", "#a8484f", "#6b7c93"]


# How each registry is written on the page, wherever it appears -- the panel headers
# on the download charts and the labels under every figure on a project tile.
REGISTRY_NAMES = {
    "bioconductor.org": "Bioconductor",
    "pypi.org": "PyPI",
    "npmjs.org": "npm",
    "cran.r-project.org": "CRAN",
    "repo1.maven.org": "Maven",
    "conda-forge.org": "conda-forge",
    "debian-13": "Debian 13 (Trixie)",
    "ubuntu-24.04": "Ubuntu 24.04",
}

def _registry_label() -> str:
    """A Vega expression turning `pypi.org/pybacting` into `PyPI`.

    Written from REGISTRY_NAMES rather than repeated by hand, so a registry added
    to that table is labelled everywhere at once. An unknown host falls through to
    itself, which is ugly on the page and therefore gets noticed.
    """
    expr = "datum.registry_id"
    for host, label in reversed(list(REGISTRY_NAMES.items())):
        expr = f"datum.registry_id === '{host}' ? '{label}' : {expr}"
    return expr


# Split `<host>/<name>` into the two things the chart needs. Done in the spec rather
# than in a new CSV column: the published long format is `metric,entity,period,value,
# partial,collected_on`, and people have that file.
SPLIT_ENTITY = [
    {"calculate": "split(datum.entity, '/')[0]", "as": "registry_id"},
    {"calculate": "substring(datum.entity, indexof(datum.entity, '/') + 1)",
     "as": "package"},
]


BASE: dict[str, Any] = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "background": "transparent",
    "width": "container",
    "height": 260,
    # Without this, the width we set is the plotting area and the axis labels are drawn
    # outside it, so every figure overflows its column by the width of the y-axis.
    "autosize": {"type": "fit", "contains": "padding"},
    "config": {
        "axis": {"labelColor": "#8a8f98", "titleColor": "#8a8f98",
                 "gridColor": "#8a8f9833", "domainColor": "#8a8f9855",
                 "tickColor": "#8a8f9855"},
        "legend": {"labelColor": "#8a8f98", "titleColor": "#8a8f98"},
        "view": {"stroke": "transparent"},
        "range": {"category": PALETTE},
    },
}


def _spec(mark: dict[str, Any], encoding: dict[str, Any], csv: str,
          transform: list | None = None, **over: Any) -> dict[str, Any]:
    # `csv` is relative to the SITE ROOT. charts.js rewrites it to an absolute URL
    # at render time, because the correct number of "../" hops differs between the
    # index page and a sub-page, and differs again under a GitHub Pages project path.
    spec = {**BASE, "data": {"url": csv, "format": {"type": "csv"}},
            "mark": mark, "encoding": encoding}
    if transform:
        spec["transform"] = transform
    spec.update(over)
    return spec


def bioc_ips() -> dict[str, Any]:
    return _spec(
        {"type": "line", "tooltip": True, "point": False},
        {"x": {"field": "period", "type": "temporal", "title": None},
         "y": {"field": "value", "type": "quantitative", "title": "Distinct IPs / month"},
         "color": {"field": "entity", "type": "nominal", "title": "Package"}},
        "data/bioc_distinct_ips_monthly.csv",
        # The in-progress month is always incomplete and would read as a cliff.
        transform=[{"filter": "datum.partial != 'true'"}],
        height=280,
    )


def releases_by_year() -> dict[str, Any]:
    return _spec(
        {"type": "bar", "tooltip": True},
        {"x": {"field": "period", "type": "ordinal", "title": "Year"},
         "y": {"field": "value", "type": "quantitative", "title": "Releases and tags"}},
        "data/releases_by_year.csv",
        transform=[{"filter": "datum.period >= '2012'"}],
    )


def rsd_mentions() -> dict[str, Any]:
    return _spec(
        {"type": "bar", "tooltip": True},
        {"y": {"field": "entity", "type": "nominal", "sort": "-x", "title": None},
         "x": {"field": "value", "type": "quantitative", "title": "Papers mentioning",
               "scale": {"type": "sqrt"}}},
        "data/rsd_mentions.csv",
        height=320,
    )


def docker_pulls() -> dict[str, Any]:
    return _spec(
        {"type": "bar", "tooltip": True},
        {"y": {"field": "entity", "type": "nominal", "sort": "-x", "title": None},
         "x": {"field": "value", "type": "quantitative", "title": "Pulls, all time"}},
        "data/docker_pulls_total.csv",
        height=280,
    )


def _by_registry(csv: str, title: str) -> dict[str, Any]:
    """Packages as bars, one panel per registry, for any per-package figure.

    Faceted rather than merged into one ranking, because a registry is not a
    category of package but the thing that did the counting. Panels keep PyPI's
    number next to PyPI's name and make the day a CRAN package appears a new panel
    rather than a bar that has to be read carefully to be told apart. The panel
    header carries the registry, so the bars themselves need no colour to say it.

    ``step`` sizing rather than a fixed height: the panel grows a row per package,
    so adding one never squeezes the others into unreadable slivers.
    """
    spec = {
        **BASE,
        "data": {"url": csv, "format": {"type": "csv"}},
        "transform": SPLIT_ENTITY + [
            {"calculate": _registry_label(), "as": "registry"}],
        "facet": {"row": {"field": "registry", "type": "nominal", "title": None,
                          "sort": list(REGISTRY_NAMES.values()),
                          "header": {"labelAngle": 0, "labelAlign": "left",
                                     "labelFontWeight": 600, "labelPadding": 2}}},
        "spec": {
            "mark": {"type": "bar", "tooltip": True},
            "height": {"step": 22},
            "encoding": {
                "y": {"field": "package", "type": "nominal", "sort": "-x",
                      "title": None},
                "x": {"field": "value", "type": "quantitative", "title": title},
            },
        },
        # Each panel ranks its own packages. A shared y scale would print every
        # package name in every panel with most of the rows empty.
        "resolve": {"scale": {"y": "independent"}, "axis": {"y": "independent"}},
    }
    # `fit` and `container` are single-view features; Vega-Lite warns and ignores
    # them on a faceted spec, and the warning is easy to miss. charts.js measures the
    # column and sets the panel width instead.
    for single_view_only in ("autosize", "width", "height"):
        spec.pop(single_view_only, None)
    return spec


def downloads_lifetime() -> dict[str, Any]:
    return _by_registry("data/package_downloads_total.csv", "Downloads, all time")


def downloads_recent() -> dict[str, Any]:
    return _by_registry("data/package_downloads_recent.csv", "Downloads, last 30 days")


def citations() -> dict[str, Any]:
    return _spec(
        {"type": "bar", "tooltip": True},
        {"y": {"field": "entity", "type": "nominal", "sort": "-x", "title": None},
         "x": {"field": "value", "type": "quantitative", "title": "Citations"}},
        "data/paper_citations.csv",
        height=280,
    )


def dependents() -> dict[str, Any]:
    return _by_registry("data/package_dependents.csv", "Dependent packages")


CHARTS = {
    "downloads_lifetime": (downloads_lifetime, "package_downloads_total"),
    "downloads_recent": (downloads_recent, "package_downloads_recent"),
    "bioc_ips": (bioc_ips, "bioc_distinct_ips_monthly"),
    "releases_by_year": (releases_by_year, "releases_by_year"),
    "rsd_mentions": (rsd_mentions, "rsd_mentions"),
    "citations": (citations, "paper_citations"),
    "docker_pulls": (docker_pulls, "docker_pulls_total"),
    "dependents": (dependents, "package_dependents"),
}
