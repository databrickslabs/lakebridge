-- Adjusted Rate (effective $/unit) from the organization rate sheet.
-- 90-day average effective rate per credit/unit, scoped to the current account and split
-- by rating type so compute ($/credit) and storage ($/TB) stay separate. EFFECTIVE_RATE
-- already reflects contract discounts.
-- Requires SNOWFLAKE.ORGANIZATION_USAGE access (ACCOUNTADMIN in an ORGADMIN-enabled
-- account, or a role granted the ORGANIZATION_USAGE_VIEWER database role).

SELECT
    REGION,
    SERVICE_LEVEL        AS TIER,
    RATING_TYPE,
    SERVICE_TYPE,
    CURRENCY,
    AVG(EFFECTIVE_RATE)  AS AVG_EFFECTIVE_RATE,
    COUNT(*)             AS RATE_DAYS,
    MIN(DATE)            AS PERIOD_START,
    MAX(DATE)            AS PERIOD_END,
    CURRENT_TIMESTAMP()  AS EXTRACT_TIMESTAMP
FROM SNOWFLAKE.ORGANIZATION_USAGE.RATE_SHEET_DAILY
WHERE ACCOUNT_NAME = CURRENT_ACCOUNT_NAME()
    AND DATE >= DATEADD('day', -90, CURRENT_DATE())
    AND IS_ADJUSTMENT = FALSE
    AND UPPER(BILLING_TYPE) = 'CONSUMPTION'
    AND RATING_TYPE != 'AI_COMPUTE'
GROUP BY REGION, SERVICE_LEVEL, RATING_TYPE, SERVICE_TYPE, CURRENCY;
