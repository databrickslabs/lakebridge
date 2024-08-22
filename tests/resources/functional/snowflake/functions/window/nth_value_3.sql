
-- snowflake sql:
SELECT tabb.col_a, tabb.col_b, nth_value(CASE WHEN tabb.col_c IN ('xyz', 'abc') THEN tabb.col_d END, 42) ignore nulls OVER (partition BY tabb.col_e ORDER BY tabb.col_f DESC RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS derived_col_a FROM schema_a.table_a taba LEFT JOIN schema_b.table_b AS tabb ON taba.col_e = tabb.col_e;

-- databricks sql:
SELECT tabb.col_a, tabb.col_b, NTH_VALUE(CASE WHEN tabb.col_c IN ('xyz', 'abc') THEN tabb.col_d END, 42) IGNORE NULLS OVER (PARTITION BY tabb.col_e ORDER BY tabb.col_f DESC NULLS FIRST RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS derived_col_a FROM schema_a.table_a AS taba LEFT JOIN schema_b.table_b AS tabb ON taba.col_e = tabb.col_e;