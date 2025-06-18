import pytest

from databricks.labs.lakebridge.discovery.tsql_table_definition import TsqlTableDefinitionService
from ..connections.helpers import get_db_manager


@pytest.fixture()
def extractor(mock_credentials):
    return get_db_manager("remorph", "mssql")


def test_tsql_get_catalog(extractor):
    tss = TsqlTableDefinitionService(extractor)
    catalogs = list(tss.get_all_catalog())
    assert catalogs is not None
    assert len(catalogs) > 0


def test_tsql_get_table_definition(extractor):
    tss = TsqlTableDefinitionService(extractor)
    table_def = tss.get_table_definition("labs_azure_sandbox_remorph")
    assert table_def is not None
