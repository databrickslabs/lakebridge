import logging

import pytest

from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step


def _config(*step_specs: tuple[str, str, str]) -> PipelineConfig:
    steps = [Step(name=n, type=t, extract_source="dummy.sql", flag=f) for n, t, f in step_specs]
    return PipelineConfig(name="test", version="1.0", extract_folder="/tmp", steps=steps)


@pytest.mark.parametrize(
    "step_specs, expect_warning, expected_in_message",
    [
        # No warning: DDL is the first active step
        ([("ddl1", "ddl", "active"), ("sql1", "sql", "active")], False, []),
        # No warning: no DDL steps at all
        ([("sql1", "sql", "active"), ("sql2", "sql", "active")], False, []),
        # No warning: inactive non-DDL before DDL has no runtime impact
        ([("sql1", "sql", "inactive"), ("ddl1", "ddl", "active"), ("sql2", "sql", "active")], False, []),
        # No warning: empty step list
        ([], False, []),
        # No warning: interleaved DDL where first active step is DDL
        (
            [
                ("ddl1", "ddl", "active"),
                ("sql1", "sql", "active"),
                ("ddl2", "ddl", "active"),
                ("sql2", "sql", "active"),
            ],
            False,
            [],
        ),
        # Warning: one active SQL step before DDL
        ([("sql1", "sql", "active"), ("ddl1", "ddl", "active")], True, ["sql1"]),
        # Warning: two active SQL steps before DDL — both named
        (
            [("sql1", "sql", "active"), ("sql2", "sql", "active"), ("ddl1", "ddl", "active")],
            True,
            ["sql1", "sql2"],
        ),
        # Warning: active python step before DDL
        ([("py1", "python", "active"), ("ddl1", "ddl", "active")], True, ["py1"]),
        # Warning: mixed active/inactive — only active non-DDL step is named
        (
            [("sql_inactive", "sql", "inactive"), ("sql_active", "sql", "active"), ("ddl1", "ddl", "active")],
            True,
            ["sql_active"],
        ),
    ],
    ids=[
        "ddl_first_no_warning",
        "no_ddl_steps_no_warning",
        "inactive_non_ddl_before_ddl_no_warning",
        "empty_steps_no_warning",
        "interleaved_ddl_first_no_warning",
        "one_active_sql_before_ddl_warns",
        "two_active_sql_before_ddl_warns",
        "python_before_ddl_warns",
        "only_active_non_ddl_named_in_warning",
    ],
)
def test_pipeline_config_ddl_order_warning(
    step_specs: list[tuple[str, str, str]],
    expect_warning: bool,
    expected_in_message: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger_name = "databricks.labs.lakebridge.assessments.profiler_config"
    with caplog.at_level(logging.WARNING, logger=logger_name):
        _config(*step_specs)

    ordering_warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING and "DDL step" in r.message]

    if expect_warning:
        assert len(ordering_warnings) == 1, f"Expected exactly one ordering warning, got: {ordering_warnings}"
        for name in expected_in_message:
            assert name in ordering_warnings[0], f"Expected '{name}' in warning: {ordering_warnings[0]}"
    else:
        assert len(ordering_warnings) == 0, f"Expected no ordering warning, got: {ordering_warnings}"
