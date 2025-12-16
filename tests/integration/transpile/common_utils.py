import difflib
from pathlib import Path
from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.transpiler.execute import transpile


def assert_sql_match(actual: str, expected: str, label: str = "SQL") -> None:
    """
    Assert that two SQL strings match using difflib for comparison.
    Shows unified diff if there are any differences.
    """
    diff = list(
        difflib.unified_diff(
            expected.strip().splitlines(keepends=True),
            actual.strip().splitlines(keepends=True),
            fromfile='expected',
            tofile='actual',
        )
    )

    if diff:
        diff_text = ''.join(diff)
        raise AssertionError(f"{label} mismatch:\n{diff_text}")


def assert_sql_outputs(output_folder: Path, expected_sql: str, expected_failure_sql: str) -> None:
    """
    Assert that SQL output files match expected content.
    Uses difflib to provide detailed comparison on mismatch.
    """
    actual_sql = (output_folder / "create_ddl.sql").read_text(encoding="utf-8")
    actual_failure_sql = (output_folder / "dummy_function.sql").read_text(encoding="utf-8")

    assert_sql_match(actual_sql, expected_sql, "DDL SQL")
    assert_sql_match(actual_failure_sql, expected_failure_sql, "Failure SQL")


async def run_transpile_and_assert(
    ws,
    lsp_engine,
    config_path,
    input_source,
    output_folder,
    source_dialect,
    expected_sql,
    expected_failure_sql,
):

    transpile_config = TranspileConfig(
        transpiler_config_path=str(config_path),
        source_dialect=source_dialect,
        input_source=str(input_source),
        output_folder=str(output_folder),
        skip_validation=False,
        catalog_name="catalog",
        schema_name="schema",
    )
    await transpile(ws, lsp_engine, transpile_config)
    assert_sql_outputs(output_folder, expected_sql, expected_failure_sql)
