import re
from importlib.metadata import requires
from pathlib import Path
from subprocess import run

import pytest
import yaml

from databricks.labs.lakebridge.assessments._constants import PLATFORM_TO_SOURCE_TECHNOLOGY_CFG
from databricks.labs.lakebridge.assessments.pipeline import PipelineClass

_REPO_ROOT = Path(__file__).parents[3]
_DISTRIBUTION = "databricks-labs-lakebridge"


def _requirement_name(requirement: str) -> str:
    """Return the canonical package name from a requirement string (e.g. 'foo~=1.2' -> 'foo')."""
    match = re.match(r"[A-Za-z0-9._-]+", requirement.strip())
    return match.group(0).lower().replace("_", "-") if match else ""


def _runtime_dependency_names() -> set[str]:
    """Return the names of the project's non-optional (non-extra) runtime dependencies."""
    return {
        _requirement_name(requirement) for requirement in requires(_DISTRIBUTION) or [] if "extra ==" not in requirement
    }


def test_step_venv_resolves_parent_package(tmp_path: Path) -> None:
    """Test that a per-step venv can import a parent package it never declares once linked."""
    create_venv = getattr(PipelineClass, "_create_venv")
    link_parent_site_packages = getattr(PipelineClass, "_link_parent_site_packages")
    venv_exec_cmd = create_venv(tmp_path / "venv")
    link_parent_site_packages(venv_exec_cmd)

    result = run(
        [venv_exec_cmd, "-c", "import databricks.labs.lakebridge"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"per-step venv could not resolve a parent package:\n{result.stderr}"


def test_step_venv_is_isolated_before_linking(tmp_path: Path) -> None:
    """Test that a freshly created venv does not see parent packages, keeping pip installs isolated."""
    create_venv = getattr(PipelineClass, "_create_venv")
    venv_exec_cmd = create_venv(tmp_path / "venv")

    result = run(
        [venv_exec_cmd, "-c", "import databricks.labs.lakebridge"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0, "venv resolved a parent package before linking; pip installs would not be isolated"


@pytest.mark.parametrize("platform", ["synapse", "mssql"])
def test_shipped_python_steps_do_not_redeclare_runtime_deps(platform: str) -> None:
    """Test that shipped Python-step configs do not re-list project runtime dependencies."""
    runtime_deps = _runtime_dependency_names()
    config = yaml.safe_load((_REPO_ROOT / PLATFORM_TO_SOURCE_TECHNOLOGY_CFG[platform]).read_text(encoding="utf-8"))

    offenders = {
        step["name"]: _requirement_name(dep)
        for step in config["steps"]
        if step.get("type") == "python"
        for dep in step.get("dependencies", [])
        if _requirement_name(dep) in runtime_deps
    }

    assert not offenders, f"{platform} python steps re-declare project runtime deps: {offenders}"
