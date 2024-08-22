-- snowflake sql:
SELECT
  lead(col1) OVER (
    PARTITION BY col1
    ORDER BY
      col2
  ) AS lead_col1
FROM
  tabl;

-- databricks sql:
SELECT
  LEAD(col1) OVER (
    PARTITION BY col1
    ORDER BY
      col2 ASC NULLS LAST ROWS BETWEEN UNBOUNDED PRECEDING
      AND UNBOUNDED FOLLOWING
  ) AS lead_col1
FROM
  tabl;
