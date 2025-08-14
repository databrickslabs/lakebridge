from databricks.labs.lakebridge.discovery.tsql_table_definition import TsqlTableDefinitionService


def test_tsql_get_catalog(test_sqlserver):
    tss = TsqlTableDefinitionService(test_sqlserver)
    catalogs = list(tss.get_all_catalog())
    assert catalogs is not None
    assert len(catalogs) > 0


def test_tsql_get_table_definition(test_sqlserver):
    tss = TsqlTableDefinitionService(test_sqlserver)
    table_def = tss.get_table_definition("labs_azure_sandbox_remorph")
    assert table_def is not None
