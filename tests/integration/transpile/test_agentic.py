import os
from pathlib import Path

import pytest
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.transpiler.agentic.converter import AgenticConverter
from databricks.labs.lakebridge.transpiler.agentic.sources import SourceFiles

pytestmark = pytest.mark.skipif(
    not os.environ.get("LAKEBRIDGE_AGENTIC_E2E"),
    reason="Converts real files with the agentic code converter, which takes minutes and compute: set "
    "LAKEBRIDGE_AGENTIC_E2E=1 to run it against a workspace where the converter is available.",
)


@pytest.mark.parametrize(("target", "extension"), [("SPARKSQL", ".sql"), ("PYSPARK", ".py")])
def test_converts_and_writes_each_result_into_its_source_folder(
    ws: WorkspaceClient, tmp_path: Path, target: str, extension: str
) -> None:
    input_source = tmp_path / "input"
    (input_source / "nested").mkdir(parents=True)
    (input_source / "orders.sql").write_text("SELECT IFF(amount > 100, 'big', 'small') AS size FROM orders;\n")
    (input_source / "nested" / "customers.sql").write_text("SELECT ZEROIFNULL(balance) AS balance FROM customers;\n")
    output_folder = tmp_path / "output"
    user_name = ws.current_user.me().user_name
    assert user_name

    result = AgenticConverter(ws).convert(
        sources=SourceFiles.collect(input_source),
        source_dialect="snowflake",
        target_technology=target,
        user_name=user_name,
        output_folder=output_folder,
        error_path=tmp_path / "errors.log",
    )
    try:
        assert result.error is None
        assert result.summary["total_files_processed"] == 2
        # The converter writes its outputs flat, and so does the local output.
        assert set(result.written.values()) == {
            output_folder / f"orders{extension}",
            output_folder / f"customers{extension}",
        }
        for path in result.written.values():
            assert path.read_text().strip()
    finally:
        ws.genie_migrate.delete(result.project)
        ws.workspace.delete(result.run_folder, recursive=True)
