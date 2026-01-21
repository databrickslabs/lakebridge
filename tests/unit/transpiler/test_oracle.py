import pytest

from ..conftest import FunctionalTestFile, get_functional_test_files


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "sample" in metafunc.fixturenames:
        samples = get_functional_test_files(metafunc.config.rootpath, suite="oracle", source="oracle")
        ids = [sample.test_name for sample in samples]
        metafunc.parametrize("sample", samples, ids=ids)


def test_oracle(dialect_context, sample: FunctionalTestFile) -> None:
    validate_source_transpile, _ = dialect_context
    validate_source_transpile(databricks_sql=sample.databricks_sql, source={"oracle": sample.source})
