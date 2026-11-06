from pathlib import Path

import pytest

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass, StepExecutionStatus
from databricks.labs.lakebridge.assessments.profiler import Profiler
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step, StepLabel


@pytest.mark.parametrize(
    ("exclude_labels", "exclude_steps", "expected_status"),
    (
        # Label excluded: skipped before the step needs a connector.
        ({StepLabel.UNREDACTED}, set(), StepExecutionStatus.SKIPPED),
        # Step excluded by name, in any case: skipped the same way.
        (set(), {"Labelled_Step"}, StepExecutionStatus.SKIPPED),
        # Nothing excluded: the step runs, and fails here because there is no connector.
        (set(), set(), StepExecutionStatus.ERROR),
    ),
)
def test_exclude(
    tmp_path: Path,
    exclude_labels: set[StepLabel],
    exclude_steps: set[str],
    expected_status: StepExecutionStatus,
) -> None:
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
        exclude_steps=exclude_steps,
    )
    results = pipeline.execute()

    assert [r.status for r in results] == [expected_status]


def test_excluding_an_unknown_step_fails_before_running(tmp_path: Path) -> None:
    step = Step(name="known_step", type="sql", extract_source=str(tmp_path / "query.sql"))
    config = PipelineConfig(name="test", version="1.0", steps=[step])

    with pytest.raises(ValueError, match="Cannot exclude unknown_step: .* steps: known_step"):
        Profiler("teradata", pipeline_configs=config).profile(
            output_folder=tmp_path, cred_file_path=tmp_path / "creds.yml", exclude_steps={"unknown_step"}
        )

    assert not list(tmp_path.iterdir())
