
-- snowflake sql:
SELECT approx_top_k(col1) AS approx_top_k_col1 FROM tabl;

-- databricks sql:
SELECT APPROX_TOP_K(col1, 1) AS approx_top_k_col1 FROM tabl;
