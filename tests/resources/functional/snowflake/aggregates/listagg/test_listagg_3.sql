-- snowflake sql:
SELECT LISTAGG(DISTINCT col3, '|')
            FROM test_table WHERE col2 > 10000;

-- databricks sql:
SELECT
    LISTAGG(DISTINCT col3, '|')
FROM test_table
WHERE
  col2 > 10000
;
