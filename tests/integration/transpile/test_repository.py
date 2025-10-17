from collections.abc import Sequence

import pytest

from databricks.labs.lakebridge.config import LSPConfigOptionV1, LSPPromptMethod
from databricks.labs.lakebridge.transpiler.repository import TranspilerInfo, TranspilerRepository


def test_user_home() -> None:
    repository = TranspilerRepository.user_home()
    assert repository is not None
    # Can be called multiple times, returns the same instance.
    assert repository is TranspilerRepository.user_home()


def test_lists_all_transpiler_names(transpiler_repository: TranspilerRepository) -> None:
    transpiler_names = transpiler_repository.all_transpiler_names()
    assert transpiler_names == {'Morpheus', 'Bladebridge'}


def test_installed_transpiler_info(transpiler_repository: TranspilerRepository) -> None:
    installed_transpilers = transpiler_repository.installed_transpilers()

    bb_overrides = LSPConfigOptionV1(
        "overrides-file",
        LSPPromptMethod.QUESTION,
        "Specify the config file to override the default[Bladebridge] config - press <enter> for none",
        default='<none>',
    )
    target_tech = LSPConfigOptionV1(
        "target-tech", LSPPromptMethod.CHOICE, "Specify which technology should be generated", ["SPARKSQL", "PYSPARK"]
    )

    assert installed_transpilers == {
        "bladebridge": TranspilerInfo(
            transpiler_name="Bladebridge",
            version="0.1.9",
            configuration_path=transpiler_repository.transpilers_path() / "bladebridge" / "lib" / "config.yml",
            dialects={
                "athena": [bb_overrides],
                "bigquery": [bb_overrides],
                "datastage": [bb_overrides, target_tech],
                "greenplum": [bb_overrides],
                "informatica (desktop edition)": [bb_overrides, target_tech],
                "mssql": [bb_overrides],
                "netezza": [bb_overrides],
                "oracle": [bb_overrides],
                "redshift": [bb_overrides],
                "snowflake": [bb_overrides],
                "synapse": [bb_overrides],
                "teradata": [bb_overrides],
            },
        ),
        "morpheus": TranspilerInfo(
            transpiler_name="Morpheus",
            version="0.4.0",
            configuration_path=transpiler_repository.transpilers_path() / "morpheus" / "lib" / "config.yml",
            dialects={
                "snowflake": [],
                "tsql": [],
            },
        ),
    }


def test_lists_all_dialects(transpiler_repository: TranspilerRepository) -> None:
    dialects = transpiler_repository.all_dialects()
    assert dialects == {
        'athena',
        'bigquery',
        'datastage',
        'greenplum',
        'informatica (desktop edition)',
        'mssql',
        'netezza',
        'oracle',
        'redshift',
        'snowflake',
        'synapse',
        'teradata',
        'tsql',
    }


def test_lists_dialect_transpilers(transpiler_repository: TranspilerRepository) -> None:
    transpilers = transpiler_repository.transpilers_with_dialect("snowflake")
    assert transpilers == {'Morpheus', 'Bladebridge'}
    transpilers = transpiler_repository.transpilers_with_dialect("datastage")
    assert transpilers == {'Bladebridge'}


@pytest.mark.parametrize(("transpiler_id", "version"), (("morpheus", "0.4.0"), ("bladebridge", "0.1.9")))
def test_get_installed_version(transpiler_repository: TranspilerRepository, transpiler_id: str, version: str) -> None:
    installed_version = transpiler_repository.get_installed_version(transpiler_id)
    assert installed_version == version


@pytest.mark.parametrize(("transpiler_id", "version"), (("morpheus", "0.4.0"), ("bladebridge", "0.1.9")))
def test_get_installed_version_given_config_path(
    transpiler_repository: TranspilerRepository, transpiler_id: str, version: str
) -> None:
    config_path = transpiler_repository.transpilers_path() / transpiler_id / "lib" / "config.yml"
    installed_version = transpiler_repository.get_installed_version_given_config_path(config_path)
    assert installed_version == version


@pytest.mark.parametrize("transpiler_name", ("Morpheus", "Bladebridge"))
def test_transpilers_config_path(transpiler_repository: TranspilerRepository, transpiler_name: str) -> None:
    config_path = transpiler_repository.transpiler_config_path(transpiler_name)
    assert config_path.name == "config.yml" and config_path.parent.name == "lib"
    assert config_path.is_file()


@pytest.mark.parametrize(
    ("transpiler_name", "source_dialect", "expected_options"),
    (
        ("Morpheus", "snowflake", []),
        (
            "Bladebridge",
            "datastage",
            [
                LSPConfigOptionV1(
                    flag="overrides-file",
                    method=LSPPromptMethod.QUESTION,
                    prompt="Specify the config file to override the default[Bladebridge] config - press <enter> for none",
                    default='<none>',
                ),
                LSPConfigOptionV1(
                    flag="target-tech",
                    method=LSPPromptMethod.CHOICE,
                    prompt="Specify which technology should be generated",
                    choices=["SPARKSQL", "PYSPARK"],
                ),
            ],
        ),
    ),
)
def test_transpiler_dialect_options(
    transpiler_repository: TranspilerRepository,
    transpiler_name: str,
    source_dialect: str,
    expected_options: Sequence[LSPConfigOptionV1],
) -> None:
    options = transpiler_repository.transpiler_config_options(transpiler_name, source_dialect)
    assert options == expected_options
