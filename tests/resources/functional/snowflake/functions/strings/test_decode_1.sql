
-- snowflake sql:
SELECT decode(column1, 1, 'one', 2, 'two', NULL, '-NULL-', 'other') AS decode_result;

-- databricks sql:
SELECT DECODE(column1, 1, 'one', 2, 'two', NULL, '-NULL-', 'other') AS decode_result;
