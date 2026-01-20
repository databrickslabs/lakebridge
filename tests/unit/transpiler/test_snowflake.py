import pytest

from ..conftest import FunctionalTestFile, FunctionalTestFileWithExpectedException, get_functional_test_files


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "failing_sample" in metafunc.fixturenames:
        samples = get_functional_test_files(
            metafunc.config.rootpath,
            suite="snowflake_expected_exceptions",
            source="snowflake",
            is_expected_exception=True,
        )
        ids = [sample.test_name for sample in samples]
        metafunc.parametrize("failing_sample", samples, ids=ids)
    if "sample" in metafunc.fixturenames:
        samples = get_functional_test_files(metafunc.config.rootpath, suite="snowflake", source="snowflake")
        ids = [sample.test_name for sample in samples]
        metafunc.parametrize("sample", samples, ids=ids)


def test_snowflake(dialect_context, sample: FunctionalTestFile) -> None:
    validate_source_transpile, _ = dialect_context
    validate_source_transpile(databricks_sql=sample.databricks_sql, source={"snowflake": sample.source}, pretty=True)


def test_snowflake_expected_exceptions(
    dialect_context, failing_sample: FunctionalTestFileWithExpectedException
) -> None:
    validate_source_transpile, _ = dialect_context
    source = {"snowflake": failing_sample.source}
    with pytest.raises(type(failing_sample.expected_exception)):
        validate_source_transpile(databricks_sql=failing_sample.databricks_sql, source=source)
