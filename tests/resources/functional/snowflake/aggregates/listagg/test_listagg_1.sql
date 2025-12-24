-- snowflake sql:
SELECT LISTAGG(col1, ' ') FROM test_table WHERE col2 > 10000;

-- databricks sql:
SELECT
    LISTAGG(col1, ' ')
FROM test_table
WHERE
  col2 > 10000
;
