from pathlib import Path

import pytest

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass, StepExecutionStatus
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step, StepLabel


@pytest.mark.parametrize(
    ("exclude_labels", "expected_status"),
    (
        # Label excluded: skipped before the step needs a connector.
        ({StepLabel.UNREDACTED}, StepExecutionStatus.SKIPPED),
        # Label not excluded: the step runs, and fails here because there is no connector.
        (set(), StepExecutionStatus.ERROR),
    ),
)
def test_exclude_labels(tmp_path: Path, exclude_labels: set[StepLabel], expected_status: StepExecutionStatus) -> None:
    query = tmp_path / "query.sql"
    query.write_text("SELECT 1", encoding="utf-8")
    step = Step(name="labelled_step", type="sql", extract_source=str(query), label="unredacted")
    config = PipelineConfig(name="test", version="1.0", steps=[step])

    pipeline = PipelineClass(
        config,
        executor=None,
        db_path=tmp_path / "extract.db",
        cred_file_path=tmp_path / "creds.yml",
        exclude_labels=exclude_labels,
    )
    results = pipeline.execute()

    assert [r.status for r in results] == [expected_status]
