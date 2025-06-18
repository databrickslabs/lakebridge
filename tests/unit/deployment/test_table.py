from pathlib import Path

from databricks.labs.lsql.backends import MockBackend

from databricks.labs.lakebridge.deployment.table import TableDeployment


def test_deploy_table_from_ddl_file():
    sql_backend = MockBackend()
    table_deployer = TableDeployment(sql_backend)
    ddl_file = Path(__file__).parent / Path("../../resources/table_deployment_test_query.sql")
    table_deployer.deploy_table_from_ddl_file("catalog", "schema", "table", ddl_file)
    assert len(sql_backend.queries) == 1
    assert sql_backend.queries[0] == ddl_file.read_text()
