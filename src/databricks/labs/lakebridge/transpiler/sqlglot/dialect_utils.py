from sqlglot import Dialects, Dialect

from databricks.labs.lakebridge.transpiler.sqlglot.parsers import oracle, presto, snowflake, tsql
from databricks.labs.lakebridge.transpiler.sqlglot.generator.databricks import Databricks

SQLGLOT_DIALECTS: dict[str, type[Dialect] | str] = {
    "athena": Dialects.ATHENA,
    "bigquery": Dialects.BIGQUERY,
    "databricks": Databricks,
    "mysql": Dialects.MYSQL,
    "netezza": Dialects.POSTGRES,
    "oracle": oracle.Oracle,
    "postgresql": Dialects.POSTGRES,
    "presto": presto.Presto,
    "redshift": Dialects.REDSHIFT,
    "snowflake": snowflake.Snowflake,
    "sqlite": Dialects.SQLITE,
    "teradata": Dialects.TERADATA,
    "trino": Dialects.TRINO,
    "tsql": tsql.Tsql,
    "mssql": Dialects.TSQL,
    "synapse": Dialects.TSQL,
    "vertica": Dialects.POSTGRES,
}


def get_dialect(dialect: str) -> Dialect:
    return Dialect.get_or_raise(SQLGLOT_DIALECTS.get(dialect))


def get_key_from_dialect(input_dialect: Dialect) -> str:
    return [source_key for source_key, dialect in SQLGLOT_DIALECTS.items() if dialect == input_dialect][0]
