from pathlib import Path

import pytest

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass, StepExecutionStatus
from databricks.labs.lakebridge.assessments.profiler import Profiler
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step


def _config(tmp_path: Path) -> PipelineConfig:
    query = tmp_path / "query.sql"
    query.write_text("SELECT 1", encoding="utf-8")
    ddl = tmp_path / "query_ddl.sql"
    ddl.write_text("CREATE TABLE labelled_step (value BIGINT)", encoding="utf-8")
    step = Step(name="labelled_step", type="sql", extract_source=str(query), ddl_source=str(ddl), label="unredacted")
    return PipelineConfig(name="test", version="1.0", steps=[step])


@pytest.mark.parametrize(
    ("exclude", "expected_status"),
    (
        # Excluded by its label or by its name: skipped before the step needs a connector.
        ({"unredacted"}, StepExecutionStatus.SKIPPED),
        ({"labelled_step"}, StepExecutionStatus.SKIPPED),
        # Not excluded: the step runs, and fails here because there is no connector.
        (set(), StepExecutionStatus.ERROR),
    ),
)
def test_exclude(tmp_path: Path, exclude: set[str], expected_status: StepExecutionStatus) -> None:
    pipeline = PipelineClass(
        _config(tmp_path),
        executor=None,
        db_path=tmp_path / "extract.db",
        cred_file_path=tmp_path / "creds.yml",
        exclude=exclude,
    )
    results = pipeline.execute()

    assert [r.status for r in results] == [expected_status]


def test_excluding_an_unknown_step_fails_before_running(tmp_path: Path) -> None:
    profiler = Profiler("synapse", pipeline_configs=_config(tmp_path))

    with pytest.raises(ValueError, match="Cannot exclude unknown_step: neither a step label nor a step"):
        profiler.profile(output_folder=tmp_path / "out", exclude={"unredacted", "unknown_step"})

    assert not (tmp_path / "out").exists()
