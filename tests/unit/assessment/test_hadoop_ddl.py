"""The Hadoop profiler's steps are consistent: each step's SQL produces the columns its DDL declares, and the pipeline
config matches the step scripts on disk.

Each step's SQL is executed against an empty ``raw_documents`` table, with the same helper macros the step runner
installs, so the check is the real DuckDB result shape rather than a parse of the SQL text.
"""

from pathlib import Path

import duckdb
import pytest

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.resources.assessments.hadoop import extract
from databricks.labs.lakebridge.resources.assessments.hadoop.extract_runs import RECORD_STEP

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HADOOP = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments/hadoop"
_PIPELINE_CONFIG = _HADOOP / "pipeline_config.yml"
_NON_STEP_MODULES = {
    "__init__",
    "ambari",
    "extract_runs",
    "kerberos",
    "cloudera_manager",
    "endpoints",
    "extract",
    "hadoop_config",
    "masking",
    "ranger",
    "rest_client",
}

# The run's final step writes no table, so it has no DDL or SQL. Every other step writes one.
_STEPS = [step.name for step in PipelineClass.load_config_from_yaml(_PIPELINE_CONFIG).steps if step.name != RECORD_STEP]
_CM_STEPS = {
    "cm_clusters",
    "cm_services",
    "cm_hosts",
    "cm_host_config",
    "cm_cluster_export",
    "cm_timeseries",
    "cm_impala_queries",
}
_RANGER_STEPS = {
    "ranger_services",
    "ranger_policies",
    "ranger_policy_items",
}
_AMBARI_STEPS = {
    "ambari_blueprint",
    "ambari_blueprint_configurations",
    "ambari_hosts",
    "ambari_services",
    "ambari_host_components",
    "ambari_stack_versions",
    "ambari_component_metrics",
}
_IMPALA_STEPS = {
    "impala_queries",
    "impala_backends",
    "impala_daemon_config",
    "impala_catalog",
    "impala_statestore",
}


def _query(step: str) -> str:
    return (_HADOOP / f"{step}.sql").read_text(encoding="utf-8").strip().rstrip(";")


def _params(sql: str) -> dict[str, str]:
    return {"distribution": "oth"} if "$distribution" in sql else {}


@pytest.mark.parametrize("step", _STEPS)
def test_step_ddl_declares_the_columns_its_query_returns(step: str) -> None:
    ddl = (_HADOOP / f"{step}_ddl.sql").read_text(encoding="utf-8").strip()
    with duckdb.connect(":memory:") as conn:
        conn.execute(ddl)
        ddl_columns = [row[0] for row in conn.execute(f"DESCRIBE {step}").fetchall()]

    sql = _query(step)
    with duckdb.connect(":memory:") as conn:
        for macro in extract.HELPER_MACROS:
            conn.execute(macro)
        conn.execute("CREATE TABLE raw_documents (doc JSON)")
        query_columns = [
            column[0] for column in conn.execute(f"SELECT * FROM ({sql}) LIMIT 0", _params(sql)).description
        ]

    assert query_columns == ddl_columns, step


def test_every_step_writes_the_table_its_name_gives() -> None:
    for step in _STEPS:
        ddl = (_HADOOP / f"{step}_ddl.sql").read_text(encoding="utf-8")
        assert ddl.split("(", 1)[0].split() == ["CREATE", "TABLE", step], step


def test_pipeline_steps_and_step_scripts_match() -> None:
    scripts = {path.stem for path in _HADOOP.glob("*.py")} - _NON_STEP_MODULES
    sql_steps = {
        path.name[: -len(".sql")]
        for path in _HADOOP.glob("*.sql")
        if not path.name.endswith(("_ddl.sql", "_after_load.sql"))
    }

    assert set(_STEPS) | {RECORD_STEP} == scripts
    assert set(_STEPS) == sql_steps
    assert len(_STEPS) == len(set(_STEPS))


def test_pipeline_config_steps_are_python_steps_with_their_scripts() -> None:
    steps = PipelineClass.load_config_from_yaml(_PIPELINE_CONFIG).steps

    assert {step.type for step in steps} == {"python"}
    for step in steps:
        assert (_REPO_ROOT / step.extract_source).is_file(), step.name
        assert step.ddl_source is None, step.name
        assert step.flag == "active", step.name
        assert step.mode == "overwrite", step.name


def test_pipeline_config_labels_and_optional_steps() -> None:
    steps = {step.name: step for step in PipelineClass.load_config_from_yaml(_PIPELINE_CONFIG).steps}

    labelled = {name for name, step in steps.items() if step.label}
    optional = {name for name, step in steps.items() if step.optional}

    # Like the original Hadoop profiler, nothing is redacted, so no step needs the `unredacted` label.
    assert not labelled
    # The Spark History Server and Cloudera Manager are optional; the YARN ResourceManager is the core of the profile.
    assert optional == (
        {"spark_applications", "spark_application_attempts"} | _CM_STEPS | _RANGER_STEPS | _IMPALA_STEPS | _AMBARI_STEPS
    )


@pytest.mark.parametrize("step", ["yarn_applications", "spark_applications"])
def test_application_tables_keep_free_text_like_the_original_profiler(step: str) -> None:
    """The original profiler redacts nothing by default; masking is opt-in through hadoop.search_replace_file."""
    code = "\n".join(line for line in _query(step).splitlines() if not line.lstrip().startswith("--"))
    assert "[REDACTED]" not in code
