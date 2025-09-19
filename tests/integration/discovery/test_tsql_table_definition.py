from databricks.labs.lakebridge.discovery.tsql_table_definition import TsqlTableDefinitionService


def test_tsql_get_catalog(sandbox_sqlserver):
    tss = TsqlTableDefinitionService(sandbox_sqlserver)
    catalogs = list(tss.get_all_catalog())
    assert catalogs is not None
    assert len(catalogs) > 0


def test_tsql_get_table_definition(sandbox_sqlserver):
    tss = TsqlTableDefinitionService(sandbox_sqlserver)
    table_def = tss.get_table_definition("labs_azure_sandbox_remorph")
    assert table_def is not None
