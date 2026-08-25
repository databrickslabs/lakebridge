from pathlib import Path

import pytest

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import DatabaseConnector, FetchResult


class RecordingConnector(DatabaseConnector):
    """Captures the query text each step sends, so tests can assert what substitution produced."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def fetch(self, query: str) -> FetchResult:
        self.queries.append(query)
        return FetchResult(columns=[], rows=[])

    def supports_streaming(self) -> bool:
        return False

    def close(self) -> None:
        pass

    def health_check(self) -> bool:
        return True


def _executed_query(
    tmp_path: Path,
    sql: str,
    *,
    variables: dict | None = None,
    cred_body: str | None = None,
    variable_overrides: dict | None = None,
) -> str:
    """Run a single SQL step through the real pipeline and return the query the connector received."""
    sql_file = tmp_path / "step.sql"
    sql_file.write_text(sql, encoding="utf-8")
    config = PipelineConfig(
        name="test",
        version="1.0",
        steps=[Step(name="step_one", type="sql", extract_source=str(sql_file))],
        variables=variables or {},
    )
    cred_file = tmp_path / ".credentials.yml"
    if cred_body is not None:
        cred_file.write_text(cred_body, encoding="utf-8")
    connector = RecordingConnector()
    PipelineClass(config, connector, tmp_path / "extract.db", cred_file, variable_overrides).execute()
    return connector.queries[0]


def test_substitutes_from_pipeline_defaults(tmp_path: Path) -> None:
    query = _executed_query(
        tmp_path,
        "SELECT TOP ${max_rows} * FROM t WHERE d >= CURRENT_DATE - INTERVAL '${lookback_days}' DAY",
        variables={"lookback_days": 7, "max_rows": 100000},
    )
    assert query == "SELECT TOP 100000 * FROM t WHERE d >= CURRENT_DATE - INTERVAL '7' DAY"


def test_noop_without_placeholders(tmp_path: Path) -> None:
    query = _executed_query(tmp_path, "SELECT * FROM t WHERE amount > 100", variables={"lookback_days": 30})
    assert query == "SELECT * FROM t WHERE amount > 100"


def test_unknown_placeholder_left_intact(tmp_path: Path) -> None:
    query = _executed_query(
        tmp_path,
        "SELECT TOP ${max_rows} * FROM t WHERE d >= ${lookback_days}",
        variables={"lookback_days": 7},
    )
    assert query == "SELECT TOP ${max_rows} * FROM t WHERE d >= 7"


def test_credentials_profiler_section_overrides_defaults(tmp_path: Path) -> None:
    query = _executed_query(
        tmp_path,
        "SELECT TOP ${max_rows} * FROM t WHERE d >= CURRENT_DATE - INTERVAL '${lookback_days}' DAY",
        variables={"lookback_days": 7, "max_rows": 100000},
        cred_body="secret_vault_type: local\nteradata:\n  host: h\n  profiler:\n"
        "    lookback_days: 30\n    max_rows: 5000000\n",
    )
    assert query == "SELECT TOP 5000000 * FROM t WHERE d >= CURRENT_DATE - INTERVAL '30' DAY"


def test_explicit_overrides_win_over_credentials(tmp_path: Path) -> None:
    query = _executed_query(
        tmp_path,
        "INTERVAL '${lookback_days}' DAY",
        variables={"lookback_days": 7},
        cred_body="teradata:\n  profiler:\n    lookback_days: 30\n",
        variable_overrides={"lookback_days": 90},
    )
    assert query == "INTERVAL '90' DAY"


def test_missing_credentials_file_falls_back_to_defaults(tmp_path: Path) -> None:
    query = _executed_query(tmp_path, "INTERVAL '${lookback_days}' DAY", variables={"lookback_days": 7})
    assert query == "INTERVAL '7' DAY"


def test_unrelated_profiler_settings_are_ignored(tmp_path: Path) -> None:
    # Synapse/BigQuery store non-substitution settings under `profiler`. A pipeline that declares
    # no matching variable must ignore them (and not fail validation on non-conforming values).
    query = _executed_query(
        tmp_path,
        "INTERVAL '${lookback_days}' DAY",
        variables={"lookback_days": 7},
        cred_body="synapse:\n  workspace:\n    name: ws\n  profiler:\n"
        "    exclude_serverless_sql_pool: true\n    redact_sql_pools_sql_text: false\n",
    )
    assert query == "INTERVAL '7' DAY"


@pytest.mark.parametrize("bad_value", ["30; DROP TABLE t", "1 OR 1=1", "'x'", "30 days", ""])
def test_unsafe_variable_values_rejected(tmp_path: Path, bad_value: str) -> None:
    config = PipelineConfig(name="test", version="1.0", steps=[], variables={"lookback_days": bad_value})
    with pytest.raises(ValueError, match="Invalid value for pipeline variable"):
        PipelineClass(config, None, tmp_path / "extract.db", tmp_path / "creds.yml")


def test_load_config_from_yaml_reads_variables(tmp_path: Path) -> None:
    config_file = tmp_path / "pipeline_config.yml"
    config_file.write_text(
        "name: t\nversion: '1.0'\nvariables:\n  lookback_days: 30\n  max_rows: 5000000\n"
        "steps:\n  - name: s\n    type: sql\n    extract_source: s.sql\n",
        encoding="utf-8",
    )
    config = PipelineClass.load_config_from_yaml(config_file)
    assert config.variables == {"lookback_days": 30, "max_rows": 5000000}
    assert isinstance(config.steps[0], Step)
