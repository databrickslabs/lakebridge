-- ##GREATEST with a single argument is simplified
--
-- Spark SQL requires GREATEST to have more than one argument.
-- When Oracle uses GREATEST with a single argument, it is redundant and
-- must be simplified to just the inner expression.
--
-- oracle sql:
SELECT GREATEST(NVL(p1_AS_OF, TO_DATE('1000-01-01', 'YYYY-MM-DD'))) AS result FROM t;

-- databricks sql:
SELECT
  COALESCE(p1_AS_OF, TO_DATE('1000-01-01')) AS result
FROM t
