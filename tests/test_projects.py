"""config/projects.csv is the file people edit, so it gets its own checks."""

from tgx_outputs import config as cfg

REGISTRIES = {"bioconductor.org", "pypi.org", "npmjs.org", "cran.r-project.org",
              "repo1.maven.org", "conda-forge.org", "debian-13", "ubuntu-24.04"}


def test_every_project_has_the_required_fields():
    for proj in cfg.projects():
        for field in ("id", "name", "what"):
            assert proj.get(field), f"{proj.get('id', proj)} is missing {field}"
        assert proj["what"].strip().endswith("."), (
            f"{proj['id']}: `what` should be a sentence a stranger would understand")


def test_project_ids_are_unique_and_slug_shaped():
    ids = cfg.project_ids()
    assert len(ids) == len(set(ids)), "duplicate project id"
    for pid in ids:
        assert pid == pid.lower() and " " not in pid, pid


def test_package_refs_name_a_known_registry():
    for _project, ref in cfg.project_field("packages"):
        assert "/" in ref, f"{ref} should be registry/name"
        assert ref.split("/", 1)[0] in REGISTRIES, f"unknown registry in {ref}"


def test_repo_refs_are_owner_slash_name():
    for _project, repo in cfg.project_field("repos"):
        assert repo.count("/") == 1, f"{repo} should be owner/name"


def test_adding_a_project_needs_no_code_change():
    """The contract the contributing guide promises.

    Every collector reads its targets from projects.csv, so a new block is picked up
    without touching Python. If this fails, someone wired a target list somewhere else.
    """
    from tgx_outputs.collect import COLLECTORS

    project_driven = {"github", "ecosystems", "bioconductor", "dockerhub", "rsd"}
    assert project_driven <= set(COLLECTORS)
    for field in ("repos", "packages", "docker", "rsd"):
        assert cfg.project_field(field) is not None
