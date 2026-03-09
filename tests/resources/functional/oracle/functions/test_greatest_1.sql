-- ##GREATEST with multiple arguments
--
-- GREATEST with two or more arguments maps directly to Databricks GREATEST.
--
-- oracle sql:
SELECT GREATEST(a, b) AS result FROM t;

-- databricks sql:
SELECT
  GREATEST(a, b) AS result
FROM t
