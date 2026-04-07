import pytest

from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step


@pytest.mark.parametrize(
    "valid_name",
    [
        "inventory",
        "usage",
        "user_data",
        "db_extract_01",
        "TABLE_NAME",
        "_private_table",
        "a",
        "a1",
        "_",
        "__init__",
        "a" * 255,  # max length
    ],
)
def test_valid_step_names(valid_name: str) -> None:
    """Test that valid step names are accepted."""
    step = Step(
        name=valid_name,
        type="sql",
        extract_source="test.sql",
    )
    assert step.name == valid_name


@pytest.mark.parametrize(
    ("invalid_name", "error_pattern"),
    [
        # Empty name
        ("", "Step name cannot be empty"),
        # Starting with number
        ("123_table", "Invalid step name"),
        # Too long (> 255 characters)
        ("a" * 256, "too long"),
        # Special characters
        ("table;drop", "Invalid step name"),
        ("user-data", "Invalid step name"),
        ("table.name", "Invalid step name"),
        ("user@data", "Invalid step name"),
        ('table"name', "Invalid step name"),
        ("user'data", "Invalid step name"),
        ("data/table", "Invalid step name"),
        ("table\\name", "Invalid step name"),
        ("user*data", "Invalid step name"),
        ("table?name", "Invalid step name"),
        ("user!data", "Invalid step name"),
        ("user data", "Invalid step name"),
        # SQL injection attempts
        ("x; DROP TABLE users; --", "Invalid step name"),
        ("x' OR '1'='1", "Invalid step name"),
        ('x"; DROP TABLE users CASCADE; --', "Invalid step name"),
        ("x/*comment*/y", "Invalid step name"),
        ("x--comment", "Invalid step name"),
        ("x;DELETE FROM sensitive_data", "Invalid step name"),
        ("x' UNION SELECT * FROM sensitive_data --", "Invalid step name"),
    ],
)
def test_invalid_step_names(invalid_name: str, error_pattern: str) -> None:
    """Test that invalid step names are rejected with appropriate error messages."""
    with pytest.raises(ValueError, match=error_pattern):
        Step(
            name=invalid_name,
            type="sql",
            extract_source="test.sql",
        )


@pytest.mark.parametrize("mode", ["append", "overwrite"])
def test_valid_modes(mode: str) -> None:
    """Test that valid modes are accepted."""
    step = Step(
        name="test_table",
        type="sql",
        extract_source="test.sql",
        mode=mode,
    )
    assert step.mode == mode


@pytest.mark.parametrize("invalid_mode", ["invalid_mode", "delete", "replace", ""])
def test_invalid_mode(invalid_mode: str) -> None:
    """Test that invalid modes are rejected."""
    with pytest.raises(ValueError, match="Invalid mode"):
        Step(
            name="test_table",
            type="sql",
            extract_source="test.sql",
            mode=invalid_mode,
        )


@pytest.mark.parametrize("step_type", ["sql", "ddl", "python"])
def test_valid_types(step_type: str) -> None:
    """Test that valid types are accepted."""
    step = Step(
        name="test_table",
        type=step_type,
        extract_source="test.sql",
    )
    assert step.type == step_type


@pytest.mark.parametrize("invalid_type", ["invalid_type", "query", "script", ""])
def test_invalid_type(invalid_type: str) -> None:
    """Test that invalid types are rejected."""
    with pytest.raises(ValueError, match="Invalid type"):
        Step(
            name="test_table",
            type=invalid_type,
            extract_source="test.sql",
        )


def test_step_copy_preserves_validation() -> None:
    """Test that copying a step preserves validation."""
    original = Step(
        name="valid_name",
        type="sql",
        extract_source="test.sql",
    )

    # Valid copy should work
    copied = original.copy(mode="overwrite")
    assert copied.name == "valid_name"
    assert copied.mode == "overwrite"

    # Invalid copy should fail validation
    with pytest.raises(ValueError, match="Invalid mode"):
        original.copy(mode="invalid")


def test_pipeline_config_with_valid_steps() -> None:
    """Test that pipeline config accepts valid steps."""
    steps = [
        Step(name="inventory", type="sql", extract_source="inventory.sql"),
        Step(name="usage", type="sql", extract_source="usage.sql"),
    ]

    config = PipelineConfig(
        name="TestPipeline",
        version="1.0",
        extract_folder="/tmp/test",
        steps=steps,
    )

    assert config.name == "TestPipeline"
    assert len(config.steps) == 2


def test_error_message_is_helpful() -> None:
    """Test that validation errors provide helpful messages."""
    with pytest.raises(ValueError) as exc_info:
        Step(
            name="bad-name",
            type="sql",
            extract_source="test.sql",
        )

    error_msg = str(exc_info.value)
    # Check that error message contains helpful information
    assert "Invalid step name" in error_msg
    assert "bad-name" in error_msg
    assert "Start with a letter or underscore" in error_msg
    assert "Contain only letters, numbers, and underscores" in error_msg
