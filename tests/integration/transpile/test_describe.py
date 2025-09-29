from databricks.labs.lakebridge.transpiler.describe import TranspilersDescription
from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository


def test_describe_installed_transpilers(transpiler_repository: TranspilerRepository) -> None:
    """Verify that the installed transpilers can be described correctly."""
    description = TranspilersDescription(transpiler_repository)

    bb_overrides = {
        "flag": "overrides-file",
        "method": "QUESTION",
        "prompt": "Specify the config file to override the default[Bladebridge] config - press <enter> for none",
        "default": "<none>",
    }
    tech_choice = {
        "flag": "target-tech",
        "method": "CHOICE",
        "prompt": "Specify which technology should be generated",
        "choices": ["SPARKSQL", "PYSPARK"],
    }

    assert description.as_json() == {
        "available-dialects": [
            "athena",
            "bigquery",
            "datastage",
            "greenplum",
            "informatica (desktop edition)",
            "mssql",
            "netezza",
            "oracle",
            "redshift",
            "snowflake",
            "synapse",
            "teradata",
            "tsql",
        ],
        "installed-transpilers": [
            {
                "name": "Bladebridge",
                "config-path": str(transpiler_repository.transpilers_path() / "bladebridge" / "lib" / "config.yml"),
                "versions": {
                    "installed": "0.1.9",
                    "latest": None,
                },
                "supported-dialects": {
                    "athena": {"options": [bb_overrides]},
                    "bigquery": {"options": [bb_overrides]},
                    "datastage": {"options": [bb_overrides, tech_choice]},
                    "greenplum": {"options": [bb_overrides]},
                    "informatica (desktop edition)": {"options": [bb_overrides, tech_choice]},
                    "mssql": {"options": [bb_overrides]},
                    "netezza": {"options": [bb_overrides]},
                    "oracle": {"options": [bb_overrides]},
                    "redshift": {"options": [bb_overrides]},
                    "snowflake": {"options": [bb_overrides]},
                    "synapse": {"options": [bb_overrides]},
                    "teradata": {"options": [bb_overrides]},
                },
            },
            {
                "name": "Morpheus",
                "config-path": str(transpiler_repository.transpilers_path() / "morpheus" / "lib" / "config.yml"),
                "versions": {
                    "installed": "0.4.0",
                    "latest": None,
                },
                "supported-dialects": {
                    "snowflake": {"options": []},
                    "tsql": {"options": []},
                },
            },
        ],
    }
