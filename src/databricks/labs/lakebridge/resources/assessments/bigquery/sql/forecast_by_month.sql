-- AI_FORECAST-based monthly cost forecast.
--
-- Adaptations from upstream `post-analysis/queries/forecast-by-month.sql`:
--   * Output table renamed `monthly_forecasted` → `monthly_forecast` so dashboard
--     datasets [7], [14], and [25] all resolve to the same name (the upstream had a
--     naming inconsistency between datasets reading `monthly_forecast` vs
--     `monthly_forecasted`).
--
-- Named parameters (bound by the job spec):
--   :bq_profiling_catalog_name — destination catalog
--   :bq_profiling_schema_name  — destination schema
--   :forecast_horizon          — `INTERVAL '<N>' MONTHS` value for AI_FORECAST horizon
CREATE OR REPLACE TABLE IDENTIFIER(
  :bq_profiling_catalog_name || '.' || :bq_profiling_schema_name || '.' || 'monthly_forecast'
) AS
WITH params AS (
  SELECT
    min(date(month_window)) AS start_date,
    max(date(month_window)) AS end_date
  FROM IDENTIFIER(
    :bq_profiling_catalog_name || '.' || :bq_profiling_schema_name || '.' || 'monthly_weighted_pricing'
  )
),
month_dim AS (
  SELECT date_format(month_date, 'yyyy-MM') AS month
  FROM params
  LATERAL VIEW explode(sequence(start_date, end_date, interval 1 month)) AS month_date
),
distincts AS (
  SELECT DISTINCT price_percentile
  FROM IDENTIFIER(
    :bq_profiling_catalog_name || '.' || :bq_profiling_schema_name || '.' || 'monthly_weighted_pricing'
  )
),
month_dim_with_distincts AS (
  SELECT * FROM distincts, month_dim
),
historical_costs AS (
  SELECT
    month_window,
    price_percentile,
    sum(db_cost) AS db_cost,
    sum(vm_cost) AS vm_cost
  FROM IDENTIFIER(
    :bq_profiling_catalog_name || '.' || :bq_profiling_schema_name || '.' || 'monthly_weighted_pricing'
  )
  GROUP BY 1, 2
),
historical_costs_no_gaps AS (
  SELECT
    dim.price_percentile,
    date(dim.month) AS month,
    CASE WHEN mthly.db_cost IS NULL THEN 0.00 ELSE mthly.db_cost END AS db_cost,
    CASE WHEN mthly.vm_cost IS NULL THEN 0.00 ELSE mthly.vm_cost END AS vm_cost,
    FALSE AS is_forecasted
  FROM month_dim_with_distincts dim
  LEFT JOIN historical_costs mthly
    ON dim.price_percentile = mthly.price_percentile
   AND dim.month = mthly.month_window
),
forecasted_costs AS (
  SELECT *
  FROM AI_FORECAST(
    TABLE(historical_costs_no_gaps),
    horizon => :forecast_horizon,
    time_col => 'month',
    value_col => ARRAY('db_cost', 'vm_cost'),
    group_col => 'price_percentile'
  )
)
SELECT
  date_format(month, 'yyyy-MM') AS month_window,
  price_percentile,
  db_cost,
  vm_cost,
  is_forecasted
FROM historical_costs_no_gaps
UNION
SELECT
  date_format(month, 'yyyy-MM') AS month_window,
  price_percentile,
  db_cost_forecast AS db_cost,
  vm_cost_forecast AS vm_cost,
  TRUE AS is_forecasted
FROM forecasted_costs;
