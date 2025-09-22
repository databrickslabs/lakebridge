from sqlglot.dialects.tsql import TSQL as SqlglotTsql


class Tsql(SqlglotTsql):
    IDENTIFIER_START = "["
    IDENTIFIER_END = "]"
