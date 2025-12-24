-- snowflake sql:
SELECT LISTAGG(col1) FROM test_table;

-- databricks sql:
SELECT
    LISTAGG(col1)
FROM test_table
;
