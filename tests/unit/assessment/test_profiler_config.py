import pytest

from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step


def test_valid_step_names():
    """Test that valid step names are accepted."""
    valid_names = [
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
    ]

    for name in valid_names:
        step = Step(
            name=name,
            type="sql",
            extract_source="test.sql",
        )
        assert step.name == name


def test_empty_step_name():
    """Test that empty step names are rejected."""
    with pytest.raises(ValueError, match="Step name cannot be empty"):
        Step(
            name="",
            type="sql",
            extract_source="test.sql",
        )


def test_step_name_with_spaces():
    """Test that step names with spaces are rejected."""
    with pytest.raises(ValueError, match="Invalid step name"):
        Step(
            name="user data",
            type="sql",
            extract_source="test.sql",
        )


@pytest.mark.parametrize(
    "invalid_name",
    [
        "table;drop",
        "user-data",
        "table.name",
        "user@data",
        'table"name',
        "user'data",
        "data/table",
        "table\\name",
        "user*data",
        "table?name",
        "user!data",
    ],
)
def test_step_name_with_special_characters(invalid_name):
    """Test that step names with special characters are rejected."""
    with pytest.raises(ValueError, match="Invalid step name"):
        Step(
            name=invalid_name,
            type="sql",
            extract_source="test.sql",
        )


@pytest.mark.parametrize(
    "malicious_name",
    [
        "x; DROP TABLE users; --",
        "x' OR '1'='1",
        'x"; DROP TABLE users CASCADE; --',
        "x/*comment*/y",
        "x--comment",
        "x;DELETE FROM sensitive_data",
    ],
)
def test_sql_injection_attempts(malicious_name):
    """Test that SQL injection attempts in step names are rejected."""
    with pytest.raises(ValueError, match="Invalid step name"):
        Step(
            name=malicious_name,
            type="sql",
            extract_source="test.sql",
        )


def test_step_name_starting_with_number():
    """Test that step names starting with numbers are rejected."""
    with pytest.raises(ValueError, match="Invalid step name"):
        Step(
            name="123_table",
            type="sql",
            extract_source="test.sql",
        )


def test_step_name_too_long():
    """Test that excessively long step names are rejected."""
    long_name = "a" * 256
    with pytest.raises(ValueError, match="too long"):
        Step(
            name=long_name,
            type="sql",
            extract_source="test.sql",
        )


def test_step_name_max_length():
    """Test that step names at max length (255) are accepted."""
    max_length_name = "a" * 255
    step = Step(
        name=max_length_name,
        type="sql",
        extract_source="test.sql",
    )
    assert step.name == max_length_name


@pytest.mark.parametrize("mode", ["append", "overwrite"])
def test_valid_modes(mode):
    """Test that valid modes are accepted."""
    step = Step(
        name="test_table",
        type="sql",
        extract_source="test.sql",
        mode=mode,
    )
    assert step.mode == mode


def test_invalid_mode():
    """Test that invalid modes are rejected."""
    with pytest.raises(ValueError, match="Invalid mode"):
        Step(
            name="test_table",
            type="sql",
            extract_source="test.sql",
            mode="invalid_mode",
        )


@pytest.mark.parametrize("step_type", ["sql", "ddl", "python"])
def test_valid_types(step_type):
    """Test that valid types are accepted."""
    step = Step(
        name="test_table",
        type=step_type,
        extract_source="test.sql",
    )
    assert step.type == step_type


def test_invalid_type():
    """Test that invalid types are rejected."""
    with pytest.raises(ValueError, match="Invalid type"):
        Step(
            name="test_table",
            type="invalid_type",
            extract_source="test.sql",
        )


def test_none_type_rejected():
    """Test that None type is rejected."""
    with pytest.raises((ValueError, TypeError)):
        Step(
            name="test_table",
            type=None,
            extract_source="test.sql",
        )


def test_step_copy_preserves_validation():
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


def test_pipeline_config_with_valid_steps():
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


def test_pipeline_config_rejects_invalid_steps():
    """Test that pipeline config rejects steps with invalid names."""
    with pytest.raises(ValueError, match="Invalid step name"):
        Step(name="bad; name", type="sql", extract_source="test.sql")


def test_prevents_table_deletion_attack():
    """Test that table deletion attacks are prevented."""
    with pytest.raises(ValueError, match="Invalid step name"):
        Step(
            name="inventory; DROP TABLE users CASCADE; --",
            type="sql",
            extract_source="test.sql",
        )


def test_prevents_boolean_injection():
    """Test that boolean-based SQL injection is prevented."""
    with pytest.raises(ValueError, match="Invalid step name"):
        Step(
            name="x' OR '1'='1",
            type="sql",
            extract_source="test.sql",
        )


def test_prevents_union_injection():
    """Test that UNION-based SQL injection is prevented."""
    with pytest.raises(ValueError, match="Invalid step name"):
        Step(
            name="x' UNION SELECT * FROM sensitive_data --",
            type="sql",
            extract_source="test.sql",
        )


def test_error_message_is_helpful():
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
