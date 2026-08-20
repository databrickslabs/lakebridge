from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ASSESSMENT_RESOURCES = _REPO_ROOT / "src/databricks/labs/lakebridge/resources/assessments"


def test_every_production_sql_step_references_existing_ddl() -> None:
    """Gate the integration branch until every source-specific DDL follow-up has landed."""
    invalid_steps: list[str] = []

    for config_path in sorted(_ASSESSMENT_RESOURCES.glob("**/pipeline_config.yml")):
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        for step in config["steps"]:
            if step["type"] != "sql":
                continue
            ddl_source = step.get("ddl_source")
            if not ddl_source:
                invalid_steps.append(f"{config_path.relative_to(_REPO_ROOT)}: {step['name']} has no ddl_source")
                continue
            if not (_REPO_ROOT / ddl_source).is_file():
                invalid_steps.append(
                    f"{config_path.relative_to(_REPO_ROOT)}: {step['name']} references missing {ddl_source}"
                )

    assert not invalid_steps, "\n" + "\n".join(invalid_steps)
