-- snowflake sql:
SELECT col3, listagg(col4, ', ') WITHIN GROUP (ORDER BY col2 DESC)
FROM
test_table
WHERE col2 > 10000 GROUP BY col3;

-- databricks sql:
SELECT
  col3,
  LISTAGG(col4, ', ') WITHIN GROUP (ORDER BY col2 DESC NULLS FIRST)
FROM test_table
WHERE
  col2 > 10000
GROUP BY
  col3;
