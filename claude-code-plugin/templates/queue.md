# Migration Queue

The Ralph loop templates read and update this table. One row per legacy object.
Keep it in git: it is the loop's memory between iterations and sessions.

Status values: `PENDING`, `IN_PROGRESS`, `PASS`, `FAIL`, `BLOCKED`, `SKIPPED`.
Lower priority numbers run first. `Depends on` lists objects that must be `PASS` first.

| # | Object | Type | Priority | Depends on | Target | Status | Attempts | Notes |
|---|--------|------|----------|------------|--------|--------|----------|-------|
| 1 | dbo.usp_LoadCustomerDim | procedure | 1 | | main.gold.dim_customer | PENDING | 0 | |
| 2 | dbo.usp_LoadSalesFact | procedure | 2 | dbo.usp_LoadCustomerDim | main.gold.fact_sales | PENDING | 0 | |
| 3 | dbo.vw_SalesSummary | view | 3 | dbo.usp_LoadSalesFact | main.gold.sales_summary | PENDING | 0 | |
