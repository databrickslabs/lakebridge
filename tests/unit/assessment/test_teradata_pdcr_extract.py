from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TERADATA_RESOURCES = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments/teradata"
_PDCR_RESOURCES = _TERADATA_RESOURCES / "pdcr"


def test_pdcr_workload_extract_uses_system_level_grain() -> None:
    sql = (_PDCR_RESOURCES / "td_pdcr_info_agg_extract.sql").read_text(encoding="utf-8")
    normalized_sql = " ".join(sql.lower().split())

    assert "date - 180" in normalized_sql
    assert "group by 1, 2" in normalized_sql
    assert "userinfo" not in normalized_sql
    assert "organization" not in normalized_sql
    assert "department" not in normalized_sql
    assert "username" not in normalized_sql


def test_pdcr_workload_extract_ddl_matches_reduced_output() -> None:
    ddl = (_PDCR_RESOURCES / "td_pdcr_info_agg_extract_ddl.sql").read_text(encoding="utf-8")
    normalized_ddl = ddl.lower()

    assert "querytype varchar" in normalized_ddl
    assert "workloadbucket varchar" in normalized_ddl
    assert "username" not in normalized_ddl
    assert "organization" not in normalized_ddl
    assert "loghour_ts" not in normalized_ddl


def test_pdcr_bi_activity_extract_is_registered() -> None:
    sql = (_PDCR_RESOURCES / "td_pdcr_bi_concurrency_extract.sql").read_text(encoding="utf-8")
    normalized_sql = " ".join(sql.lower().split())

    assert "date - 180" in normalized_sql
    assert "avgbiqueriesperminute" in normalized_sql
    assert "group by" not in normalized_sql

    with (_TERADATA_RESOURCES / "pipeline_config.yml").open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)

    matching_steps = [step for step in config["steps"] if step["name"] == "td_pdcr_bi_concurrency_extract"]
    assert [step["type"] for step in matching_steps] == ["ddl", "sql"]
    assert matching_steps[1]["optional"] is True
