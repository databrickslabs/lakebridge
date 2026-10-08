---
description: Expert reference for converting T-SQL stored procedures, functions and views to Databricks SQL / PySpark
argument-hint: "[question or construct]"
---

# T-SQL → Databricks Conversion Expert

Answer the user's question (`$ARGUMENTS`). When the question is about a specific object, read its
source first; the source is the blueprint.

## Type mappings

| T-SQL | Databricks | Note |
|---|---|---|
| `VARCHAR(n)`, `NVARCHAR(n)`, `CHAR(n)`, `TEXT` | `STRING` | Trailing-space semantics differ for `CHAR` |
| `INT`, `BIGINT`, `SMALLINT` | same | |
| `TINYINT` | `SMALLINT` | T-SQL `TINYINT` is 0–255; Databricks `TINYINT` is −128–127 |
| `BIT` | `BOOLEAN` | |
| `DECIMAL(p,s)`, `NUMERIC(p,s)` | `DECIMAL(p,s)` | Keep precision exactly |
| `MONEY`, `SMALLMONEY` | `DECIMAL(19,4)`, `DECIMAL(10,4)` | |
| `FLOAT`, `REAL` | `DOUBLE`, `FLOAT` | |
| `DATETIME`, `DATETIME2`, `SMALLDATETIME` | `TIMESTAMP` (or `TIMESTAMP_NTZ` for wall-clock values) | `DATETIME` rounds to 1/300 s |
| `DATETIMEOFFSET` | `TIMESTAMP` | Normalize to UTC on load |
| `DATE`, `TIME` | `DATE`, `STRING` | No `TIME` type |
| `UNIQUEIDENTIFIER` | `STRING` | Normalize case |
| `VARBINARY`, `IMAGE` | `BINARY` | |
| `GEOGRAPHY`, `GEOMETRY` | `GEOGRAPHY` / `GEOMETRY`, or WKT `STRING` | |

## Function mappings

| T-SQL | Databricks |
|---|---|
| `GETDATE()`, `SYSDATETIME()` | `current_timestamp()` |
| `GETUTCDATE()` | `current_timestamp()` (session TZ = UTC) |
| `ISNULL(a,b)` | `coalesce(a,b)` |
| `DATEADD(part,n,d)` | `dateadd(part,n,d)` / `timestampadd` |
| `DATEDIFF(part,a,b)` | `date_diff(part,a,b)` / `timestampdiff(part,a,b)` — T-SQL counts boundaries crossed, Databricks counts whole units, so `DATEDIFF(day, '23:59', '00:01')` differs |
| `CONVERT(type, x, style)` | `cast` + `date_format`/`to_timestamp` with explicit pattern |
| `LEN(s)` | `length(rtrim(s))` (T-SQL `LEN` ignores trailing spaces) |
| `CHARINDEX(a,s)` | `instr(s,a)` or `locate(a,s)` |
| `STRING_AGG(x, ',')` | `array_join(collect_list(x), ',')` (order not guaranteed without `array_sort`) |
| `IIF(c,a,b)` | `if(c,a,b)` |
| `TOP n` | `LIMIT n` |
| `NEWID()` | `uuid()` |

## Procedural patterns

| Pattern | Conversion |
|---|---|
| `CURSOR` loops over rows | Window functions (`lag`, `lead`, `row_number`, running `sum`) or a set-based join. Keep a loop only for true sequential dependencies. |
| `#temp` / `@table` variables | CTEs or `createOrReplaceTempView`. Persist to a Delta table only if reused across tasks. |
| `MERGE` | `MERGE INTO` on Delta with the same `ON` keys, same `WHEN MATCHED` conditions and same column list |
| Multi-step `UPDATE ... FROM` after a MERGE | Keep them as separate ordered steps; they often backfill columns the MERGE left null |
| Dynamic SQL (`sp_executesql`) | Parameterized notebook/job widgets, or generate SQL in Python with an allow-list of identifiers |
| `TRY...CATCH` + transactions | Delta writes are atomic per statement. Make the job idempotent and rerunnable instead of relying on multi-statement transactions. |
| `@@ROWCOUNT` checks | `num_affected_rows` from the `MERGE` result, or a count on the change set |
| Scalar UDFs | SQL UDFs in Unity Catalog (`CREATE FUNCTION`) or inline expressions |
| Calls to other procedures | Separate tasks in the job with explicit dependencies |

## Things transpilers commonly get wrong

- `LEFT JOIN` + `WHERE` on the right table (turning it into an inner join) or the reverse.
- `NULL` handling in `NOT IN` and in string concatenation: T-SQL `CONCAT()` treats `NULL` as an empty
  string, but Databricks `concat()` returns `NULL` if any argument is `NULL`. Use `concat_ws('', ...)` or
  `coalesce` each argument.
- Implicit conversions in comparisons between strings and numbers.
- Integer division (`5/2 = 2` in T-SQL, `2.5` in Databricks).
- Collation-driven case-insensitive comparisons. Databricks string comparisons are case-sensitive by
  default (identifiers are not); add `lower()` or a collation where the legacy relied on `_CI_` collations.
- `ORDER BY` in views or subqueries being relied on for "first row" logic. Make it explicit.
