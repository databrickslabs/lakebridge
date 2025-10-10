from pathlib import Path
from databricks.labs.lakebridge.config import TranspileConfig
from databricks.labs.lakebridge.transpiler.execute import transpile


def assert_sql_outputs(output_folder: Path, expected_sql: str, expected_failure_sql: str) -> None:
    actual_sql = (output_folder / "create_ddl.sql").read_text(encoding="utf-8")
    actual_failure_sql = (output_folder / "dummy_function.sql").read_text(encoding="utf-8")

    assert actual_sql.strip() == expected_sql.strip()
    assert actual_failure_sql.strip() == expected_failure_sql.strip()


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
