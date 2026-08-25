from pathlib import Path

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import DatabaseConnector, FetchResult


class RecordingConnector(DatabaseConnector):
    """Captures the query text and bind parameters each step sends, so tests can assert both."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def fetch(self, query: str, parameters=None) -> FetchResult:
        self.calls.append((query, dict(parameters or {})))
        return FetchResult(columns=[], rows=[])

    def supports_streaming(self) -> bool:
        return False

    def close(self) -> None:
        pass

    def health_check(self) -> bool:
        return True


def _run_step(
    tmp_path: Path,
    sql: str,
    *,
    parameters: dict | None = None,
    cred_body: str | None = None,
    parameter_overrides: dict | None = None,
) -> tuple[str, dict]:
    """Run a single SQL step through the real pipeline; return the (query, bind parameters) it received."""
    sql_file = tmp_path / "step.sql"
    sql_file.write_text(sql, encoding="utf-8")
    config = PipelineConfig(
        name="test",
        version="1.0",
        steps=[Step(name="step_one", type="sql", extract_source=str(sql_file))],
        parameters=parameters or {},
    )
    cred_file = tmp_path / ".credentials.yml"
    if cred_body is not None:
        cred_file.write_text(cred_body, encoding="utf-8")
    connector = RecordingConnector()
    PipelineClass(config, connector, tmp_path / "extract.db", cred_file, parameter_overrides).execute()
    return connector.calls[0]


_QUERY = "SELECT * FROM t WHERE d >= CURRENT_DATE - :lookback_days QUALIFY rn <= :max_rows"


def test_query_text_is_sent_verbatim(tmp_path: Path) -> None:
    # The values are bound by the driver, never spliced into the SQL: the text must be unchanged.
    query, _ = _run_step(tmp_path, _QUERY, parameters={"lookback_days": 7, "max_rows": 100000})
    assert query == _QUERY


def test_binds_pipeline_default_parameters(tmp_path: Path) -> None:
    _, params = _run_step(tmp_path, _QUERY, parameters={"lookback_days": 7, "max_rows": 100000})
    # Native types are preserved (ints, not stringified) so the driver binds them as integers.
    assert params == {"lookback_days": 7, "max_rows": 100000}


def test_credentials_profiler_section_overrides_defaults(tmp_path: Path) -> None:
    _, params = _run_step(
        tmp_path,
        _QUERY,
        parameters={"lookback_days": 7, "max_rows": 100000},
        cred_body="secret_vault_type: local\nteradata:\n  host: h\n  profiler:\n"
        "    lookback_days: 30\n    max_rows: 5000000\n",
    )
    assert params == {"lookback_days": 30, "max_rows": 5000000}


def test_explicit_overrides_win_over_credentials(tmp_path: Path) -> None:
    _, params = _run_step(
        tmp_path,
        _QUERY,
        parameters={"lookback_days": 7, "max_rows": 100000},
        cred_body="teradata:\n  profiler:\n    lookback_days: 30\n",
        parameter_overrides={"lookback_days": 90},
    )
    assert params == {"lookback_days": 90, "max_rows": 100000}


def test_missing_credentials_file_falls_back_to_defaults(tmp_path: Path) -> None:
    _, params = _run_step(tmp_path, _QUERY, parameters={"lookback_days": 7, "max_rows": 100000})
    assert params == {"lookback_days": 7, "max_rows": 100000}


def test_unrelated_profiler_settings_are_ignored(tmp_path: Path) -> None:
    # Synapse/BigQuery store non-parameter settings under `profiler`. A pipeline that declares no
    # matching parameter must ignore them rather than binding them into unrelated queries.
    _, params = _run_step(
        tmp_path,
        "INTERVAL :lookback_days DAY",
        parameters={"lookback_days": 7},
        cred_body="synapse:\n  workspace:\n    name: ws\n  profiler:\n"
        "    exclude_serverless_sql_pool: true\n    redact_sql_pools_sql_text: false\n",
    )
    assert params == {"lookback_days": 7}


def test_load_config_from_yaml_reads_parameters(tmp_path: Path) -> None:
    config_file = tmp_path / "pipeline_config.yml"
    config_file.write_text(
        "name: t\nversion: '1.0'\nparameters:\n  lookback_days: 30\n  max_rows: 5000000\n"
        "steps:\n  - name: s\n    type: sql\n    extract_source: s.sql\n",
        encoding="utf-8",
    )
    config = PipelineClass.load_config_from_yaml(config_file)
    assert config.parameters == {"lookback_days": 30, "max_rows": 5000000}
    assert isinstance(config.steps[0], Step)
