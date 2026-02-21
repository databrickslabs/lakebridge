
-- snowflake sql:
SELECT regexp_like(col1, 'Users.*') AS regexp_like_col1 FROM tabl;

-- databricks sql:
SELECT REGEXP_LIKE(col1, 'Users.*') AS regexp_like_col1 FROM tabl;
