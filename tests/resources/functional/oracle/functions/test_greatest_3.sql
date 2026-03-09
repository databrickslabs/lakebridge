-- ##GREATEST with a single column argument is simplified
--
-- Spark SQL requires GREATEST to have more than one argument.
-- When Oracle uses GREATEST with a single column, the wrapper is removed.
--
-- oracle sql:
SELECT GREATEST(col1) AS result FROM t;

-- databricks sql:
SELECT
  col1 AS result
FROM t
