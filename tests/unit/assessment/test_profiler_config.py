import pytest

from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step


def _sql(**kwargs) -> Step:
    defaults = dict(name="test_table", type="sql", extract_source="test.sql", ddl_source="test_ddl.sql")
    defaults.update(kwargs)
    return Step(**defaults)


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
    step = _sql(name=valid_name)
    assert step.name == valid_name


@pytest.mark.parametrize(
    ("invalid_name", "error_pattern"),
    [
        ("", "Step name cannot be empty"),
        ("123_table", "Invalid step name"),
        ("a" * 256, "too long"),
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
        _sql(name=invalid_name)


@pytest.mark.parametrize("mode", ["append", "overwrite"])
def test_valid_modes(mode: str) -> None:
    """Test that valid modes are accepted."""
    step = _sql(mode=mode)
    assert step.mode == mode


@pytest.mark.parametrize("invalid_mode", ["invalid_mode", "delete", "replace", ""])
def test_invalid_mode(invalid_mode: str) -> None:
    """Test that invalid modes are rejected."""
    with pytest.raises(ValueError, match="Invalid mode"):
        _sql(mode=invalid_mode)


@pytest.mark.parametrize("step_type", ["python", "source_ddl"])
def test_valid_non_sql_types(step_type: str) -> None:
    """Test that non-sql types are accepted without ddl_source."""
    step = Step(name="test_table", type=step_type, extract_source="test.sql")
    assert step.type == step_type
    assert step.ddl_source is None


def test_valid_sql_type_requires_ddl_source() -> None:
    step = _sql()
    assert step.type == "sql"
    assert step.ddl_source == "test_ddl.sql"


def test_sql_without_ddl_source_rejected() -> None:
    with pytest.raises(ValueError, match="requires ddl_source"):
        Step(name="test_table", type="sql", extract_source="test.sql")


def test_ddl_type_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid type"):
        Step(name="test_table", type="ddl", extract_source="test.sql")


def test_non_sql_with_ddl_source_rejected() -> None:
    with pytest.raises(ValueError, match="must not set ddl_source"):
        Step(name="test_table", type="python", extract_source="x.py", ddl_source="x_ddl.sql")


@pytest.mark.parametrize("invalid_type", ["invalid_type", "query", "script", "", "ddl"])
def test_invalid_type(invalid_type: str) -> None:
    """Test that invalid types are rejected."""
    with pytest.raises(ValueError, match="Invalid type"):
        Step(
            name="test_table",
            type=invalid_type,
            extract_source="test.sql",
        )


@pytest.mark.parametrize("optional", [True, False])
def test_valid_optional(optional: bool) -> None:
    """Test that boolean optional values are accepted."""
    step = _sql(optional=optional)
    assert step.optional is optional


def test_optional_defaults_false() -> None:
    """Test that optional defaults to False so existing steps stay required."""
    step = _sql()
    assert step.optional is False


@pytest.mark.parametrize("invalid_optional", ["true", 1, None])
def test_invalid_optional(invalid_optional: object) -> None:
    """Test that non-boolean optional values are rejected."""
    with pytest.raises(ValueError, match="Invalid optional value"):
        _sql(optional=invalid_optional)  # type: ignore[arg-type]


def test_step_copy_preserves_validation() -> None:
    """Test that copying a step preserves validation."""
    original = _sql(name="valid_name")

    copied = original.copy(mode="overwrite")
    assert copied.name == "valid_name"
    assert copied.mode == "overwrite"

    with pytest.raises(ValueError, match="Invalid mode"):
        original.copy(mode="invalid")


def test_pipeline_config_with_valid_steps() -> None:
    """Test that pipeline config accepts valid steps."""
    steps = [
        _sql(name="inventory", extract_source="inventory.sql", ddl_source="inventory_ddl.sql"),
        _sql(name="usage", extract_source="usage.sql", ddl_source="usage_ddl.sql"),
    ]

    config = PipelineConfig(
        name="TestPipeline",
        version="1.0",
        steps=steps,
    )

    assert config.name == "TestPipeline"
    assert len(config.steps) == 2


def test_error_message_is_helpful() -> None:
    """Test that validation errors provide helpful messages."""
    with pytest.raises(ValueError) as exc_info:
        _sql(name="bad-name")

    error_msg = str(exc_info.value)
    assert "Invalid step name" in error_msg
    assert "bad-name" in error_msg
    assert "Start with a letter or underscore" in error_msg
    assert "Contain only letters, numbers, and underscores" in error_msg
