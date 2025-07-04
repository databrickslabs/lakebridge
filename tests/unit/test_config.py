import pytest

from databricks.labs.blueprint.installation import MockInstallation

from databricks.labs.lakebridge.config import TranspileConfig


def test_transpiler_config_default_serialization() -> None:
    """Verify that the default TranspileConfig can be serialized and deserialized correctly."""
    config = TranspileConfig()

    installation = MockInstallation()
    installation.save(config)

    loaded = installation.load(TranspileConfig)
    assert loaded == config


def test_typical_transpiler_config() -> None:
    """Verify that a typical TranspileConfig dataclass can be saved and loaded correctly."""
    config = TranspileConfig(
        transpiler_config_path="/a/path/to/config.yml",
        source_dialect="value with spaces",
        input_source="/a/path",
        output_folder="/another/path",
        error_file_path="file_without_path.log",
        skip_validation=True,
        catalog_name="remorph",
        schema_name="transpiler",
        sdk_config={
            "foo": "bar",
            "not_a_number": "1",
        },
        transpiler_options={
            "overrides-file": None,
            "something_else": "value",
        },
    )

    installation = MockInstallation()
    installation.save(config)

    loaded = installation.load(TranspileConfig)
    assert loaded == config


# TODO: Disabled due to issues with forward references in dataclasses with Python >= 3.12.4.
@pytest.mark.skip(reason="Enable once databrickslabs/blueprint#252 is merged, and bump the dependency version.")
def test_complete_transpiler_config() -> None:
    """Verify that a complete TranspileConfig dataclass can be saved and loaded correctly."""
    config = TranspileConfig(
        transpiler_config_path="/a/path/to/config.yml",
        source_dialect="value with spaces",
        input_source="/a/path",
        output_folder="/another/path",
        error_file_path="file_without_path.log",
        skip_validation=True,
        catalog_name="remorph",
        schema_name="transpiler",
        sdk_config={
            "foo": "bar",
            "not_a_number": "1",
        },
        transpiler_options={
            "overrides-file": None,
            "nested": {"key": "value", "list": [1, 2, 3], "nested_further": {}},
            "nasty_number": 0.1,
        },
    )

    installation = MockInstallation()
    installation.save(config)

    loaded = installation.load(TranspileConfig)
    assert loaded == config
