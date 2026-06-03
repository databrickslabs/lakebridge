import sys
from pathlib import Path

import pytest
import yaml

from databricks.labs.lakebridge.assessments._constants import PLATFORM_TO_SOURCE_TECHNOLOGY_CFG
from databricks.labs.lakebridge.assessments.pipeline import PipelineClass

_REPO_ROOT = Path(__file__).parents[3]


def test_python_step_runs_under_parent_interpreter(tmp_path: Path) -> None:
    """A Python step runs with the profiler's own interpreter and resolves parent packages with no per-step install."""
    script = tmp_path / "step.py"
    script.write_text(
        "import json\n"
        # Resolved from the parent install; the step never declares it.
        "import databricks.labs.lakebridge\n" "print(json.dumps({'status': 'success', 'message': 'ok'}))\n",
        encoding="utf-8",
    )

    run_python_script = getattr(PipelineClass, "_run_python_script")
    # Should not raise: the script imports a parent package and reports success, proving the step
    # resolves dependencies from sys.executable without creating or populating a per-step venv.
    run_python_script(sys.executable, script, str(tmp_path / "out.db"), "unused-credentials.yml")


@pytest.mark.parametrize("platform", ["synapse", "mssql"])
def test_shipped_python_steps_declare_no_dependencies(platform: str) -> None:
    """Shipped Python-step configs declare no per-step dependencies; everything resolves from the install."""
    config = yaml.safe_load((_REPO_ROOT / PLATFORM_TO_SOURCE_TECHNOLOGY_CFG[platform]).read_text(encoding="utf-8"))

    offenders = {
        step["name"]: step["dependencies"]
        for step in config["steps"]
        if step.get("type") == "python" and step.get("dependencies")
    }

    assert not offenders, f"{platform} python steps still declare per-step dependencies: {offenders}"
