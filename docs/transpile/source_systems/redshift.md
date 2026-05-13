# Amazon Redshift to Databricks

## Conversion information[​](#conversion-information "Direct link to Conversion information")

* **Transpiler**: BladeBridge - Target: Databricks SQL (experimental)
* **Transpiler**: Switch - Target: Databricks Notebook ()experimental)
* **Input format**: SQL files (.sql)

### Supported Redshift Versions[​](#supported-redshift-versions "Direct link to Supported Redshift Versions")

* Amazon Redshift (all versions)
* Amazon Redshift Serverless

### Input Requirements[​](#input-requirements "Direct link to Input Requirements")

Export your Redshift SQL scripts:

1. **Schema Scripts**: DDL statements (CREATE TABLE, CREATE VIEW, etc.)
2. **Stored Procedures**: Procedural SQL code
3. **Query Scripts**: SELECT, INSERT, UPDATE, DELETE statements
4. **ETL Scripts**: Data transformation logic

**Export from Redshift:**

```sql
-- Export table DDL
SELECT
    schemaname,
    tablename,
    ddl
FROM pg_get_table_def('schema_name', 'table_name');

-- Export view definitions
SELECT
    schemaname,
    viewname,
    definition
FROM pg_views
WHERE schemaname = 'your_schema';

```

***

## SQL Features[​](#sql-features "Direct link to SQL Features")

### Data Types - Supported[​](#data-types---supported "Direct link to Data Types - Supported")

| Redshift Type                         | Databricks Type       | Notes                             |
| ------------------------------------- | --------------------- | --------------------------------- |
| **SMALLINT / INT2**                   | INT                   | 2-byte integer                    |
| **INTEGER / INT / INT4**              | INT                   | 4-byte integer                    |
| **BIGINT / INT8**                     | BIGINT                | 8-byte integer                    |
| **DECIMAL / NUMERIC**                 | NUMERIC               | Precision preserved               |
| **REAL / FLOAT4**                     | FLOAT                 | Single precision                  |
| **DOUBLE PRECISION / FLOAT8 / FLOAT** | FLOAT                 | Double precision                  |
| **BOOLEAN / BOOL**                    | BOOLEAN               | True/false                        |
| **CHAR / CHARACTER**                  | STRING                | Fixed-length string               |
| **VARCHAR / CHARACTER VARYING**       | STRING                | Variable-length string            |
| **NCHAR / NVARCHAR**                  | STRING                | Unicode strings                   |
| **TEXT**                              | STRING                | Unlimited length                  |
| **DATE**                              | DATE                  | Date only                         |
| **TIMESTAMP**                         | TIMESTAMP             | Date and time                     |
| **TIMESTAMPTZ**                       | TIMESTAMP             | Timestamp with timezone converted |
| **TIMETZ**                            | TIMESTAMP             | Time with timezone converted      |
| **VARBYTE / VARBINARY**               | BINARY                | Binary data                       |
| **IDENTITY columns**                  | GENERATED AS IDENTITY | Auto-incrementing columns         |

### Data Types - Unsupported[​](#data-types---unsupported "Direct link to Data Types - Unsupported")

| Redshift Type | Reason                    | Workaround                                                         |
| ------------- | ------------------------- | ------------------------------------------------------------------ |
| **SUPER**     | Semi-structured data type | Use STRING or VARIANT with manual parsing                          |
| **HLLSKETCH** | HyperLogLog sketches      | Use approx\_count\_distinct() or custom UDFs                       |
| **GEOMETRY**  | Spatial data              | Migrate to Databricks spatial functions with STRING representation |

***

## SQL Functions[​](#sql-functions "Direct link to SQL Functions")

### Date & Time Functions - Supported[​](#date--time-functions---supported "Direct link to Date & Time Functions - Supported")

| Redshift Function                           | Databricks Equivalent                                          | Notes                        |
| ------------------------------------------- | -------------------------------------------------------------- | ---------------------------- |
| **GETDATE()**                               | CURRENT\_TIMESTAMP                                             | Current timestamp            |
| **SYSDATE**                                 | CURRENT\_TIMESTAMP()                                           | Current timestamp            |
| **DATE\_PART('year', date)**                | YEAR(date)                                                     | Extract year                 |
| **DATE\_PART('month', date)**               | MONTH(date)                                                    | Extract month                |
| **DATE\_PART('day', date)**                 | DAY(date)                                                      | Extract day                  |
| **DATE\_PART('hour', date)**                | HOUR(date)                                                     | Extract hour                 |
| **DATE\_PART('minute', date)**              | MINUTE(date)                                                   | Extract minute               |
| **DATE\_PART('second', date)**              | SECOND(date)                                                   | Extract second               |
| **DATE\_PART('quarter', date)**             | QUARTER(date)                                                  | Extract quarter              |
| **DATE\_PART('week', date)**                | WEEKOFYEAR(date)                                               | Week number                  |
| **DATE\_PART('dow', date)**                 | DAYOFWEEK(date)                                                | Day of week                  |
| **DATE\_PART('doy', date)**                 | DAYOFYEAR(date)                                                | Day of year                  |
| **DATE\_PART('epoch', date)**               | UNIX\_TIMESTAMP(date)                                          | Seconds since epoch          |
| **DATEDIFF('day', start, end)**             | DATEDIFF(DAY, start, end)                                      | Difference in days           |
| **DATEDIFF('month', start, end)**           | MONTH(end) - MONTH(start) + 12 \* (YEAR(end) - YEAR(start))    | Difference in months         |
| **DATEDIFF('year', start, end)**            | YEAR(end) - YEAR(start)                                        | Difference in years          |
| **DATEDIFF('hour', start, end)**            | (UNIX\_TIMESTAMP(end) - UNIX\_TIMESTAMP(start)) / 3600         | Difference in hours          |
| **DATEDIFF('second', start, end)**          | UNIX\_TIMESTAMP(end) - UNIX\_TIMESTAMP(start)                  | Difference in seconds        |
| **DATEADD('day', n, date)**                 | DATE\_ADD(date, n)                                             | Add days                     |
| **DATEADD('month', n, date)**               | ADD\_MONTHS(date, n)                                           | Add months                   |
| **DATEADD('year', n, date)**                | ADD\_MONTHS(date, n \* 12)                                     | Add years                    |
| **DATEADD('hour', n, date)**                | TIMESTAMPADD(HOUR, n, date)                                    | Add hours                    |
| **EXTRACT(YEAR FROM date)**                 | EXTRACT(YEAR FROM date)                                        | Extract year                 |
| **TRUNC(date)**                             | TRUNC(date, 'DD')                                              | Truncate to day              |
| **CONVERT\_TIMEZONE(tz, ts)**               | FROM\_UTC\_TIMESTAMP(ts, tz)                                   | Timezone conversion (2 args) |
| **CONVERT\_TIMEZONE(src\_tz, tgt\_tz, ts)** | FROM\_UTC\_TIMESTAMP(TO\_UTC\_TIMESTAMP(ts, src\_tz), tgt\_tz) | Timezone conversion (3 args) |

### String Functions - Supported[​](#string-functions---supported "Direct link to String Functions - Supported")

| Redshift Function                 | Databricks Equivalent                                   | Notes                   |
| --------------------------------- | ------------------------------------------------------- | ----------------------- |
| **LEN(str)**                      | CHAR\_LENGTH(str)                                       | String length           |
| **CHAR\_LENGTH(str)**             | CHAR\_LENGTH(str)                                       | String length           |
| **TEXTLEN(str)**                  | CHAR\_LENGTH(str)                                       | String length           |
| **STRPOS(str, substr)**           | POSITION(substr IN str)                                 | Find substring position |
| **CHARINDEX(substr, str)**        | INSTR(str, substr)                                      | Find substring position |
| **REPLICATE(str, n)**             | REPEAT(str, n)                                          | Repeat string           |
| **REPLACE\_CHARS(str, old, new)** | REPLACE(str, old, new)                                  | Replace characters      |
| **SPLIT\_PART(str, delim, part)** | SPLIT(str, delim)\[part-1]                              | Split and get part      |
| **REGEXP\_SUBSTR(str, pattern)**  | REGEXP\_EXTRACT(str, pattern, 0)                        | Extract using regex     |
| **REGEXP\_COUNT(str, pattern)**   | LENGTH(str) - LENGTH(REGEXP\_REPLACE(str, pattern, '')) | Count regex matches     |
| **CHR(n)**                        | CHAR(n)                                                 | ASCII code to character |
| **QUOTE\_LITERAL(str)**           | CONCAT(''', REGEXP\_REPLACE(str, ''', ''''), ''')       | Quote string literal    |

### Aggregate Functions - Supported[​](#aggregate-functions---supported "Direct link to Aggregate Functions - Supported")

| Redshift Function                                          | Databricks Equivalent                             | Notes                                |
| ---------------------------------------------------------- | ------------------------------------------------- | ------------------------------------ |
| **LISTAGG(col, delim)**                                    | ARRAY\_JOIN(COLLECT\_LIST(col), delim)            | Concatenate strings                  |
| **LISTAGG(DISTINCT col, delim) WITHIN GROUP (ORDER BY x)** | ARRAY\_JOIN(ARRAY\_DISTINCT(...), delim)          | Distinct concatenation with ordering |
| **MEDIAN(col)**                                            | PERCENTILE\_CONT(0.5) WITHIN GROUP (ORDER BY col) | Median value                         |
| **STDDEV\_POP(col)**                                       | STDDEV\_POP(col)                                  | Population standard deviation        |
| **STDDEV\_SAMP(col)**                                      | STDDEV\_SAMP(col)                                 | Sample standard deviation            |
| **VAR\_POP(col)**                                          | VAR\_POP(col)                                     | Population variance                  |
| **VAR\_SAMP(col)**                                         | VAR\_SAMP(col)                                    | Sample variance                      |

### JSON Functions - Supported[​](#json-functions---supported "Direct link to JSON Functions - Supported")

| Redshift Function                                  | Databricks Equivalent                                                | Notes                       |
| -------------------------------------------------- | -------------------------------------------------------------------- | --------------------------- |
| **JSON\_EXTRACT\_PATH\_TEXT(json, 'key')**         | GET\_JSON\_OBJECT(json, '$.key')                                     | Extract JSON value (2 args) |
| **JSON\_EXTRACT\_PATH\_TEXT(json, 'k1', 'k2')**    | GET\_JSON\_OBJECT(json, '$.k1.k2')                                   | Extract nested JSON value   |
| **JSON\_EXTRACT\_ARRAY\_ELEMENT\_TEXT(json, idx)** | GET\_JSON\_OBJECT(json, '$\[idx]')                                   | Extract array element       |
| **JSON\_PARSE(str)**                               | FROM\_JSON(str, schema)                                              | Parse JSON string           |
| **JSON\_QUERY(json, path)**                        | JSON\_EXTRACT\_SCALAR(json, path)                                    | Query JSON                  |
| **IS\_VALID\_JSON(str)**                           | CASE WHEN TRY\_PARSE\_JSON(str) IS NOT NULL THEN TRUE ELSE FALSE END | Validate JSON               |

### Conditional Functions - Supported[​](#conditional-functions---supported "Direct link to Conditional Functions - Supported")

| Redshift Function                     | Databricks Equivalent                                       | Notes                  |
| ------------------------------------- | ----------------------------------------------------------- | ---------------------- |
| **NVL(expr1, expr2)**                 | COALESCE(expr1, expr2)                                      | Null value replacement |
| **ISNULL(expr1, expr2)**              | COALESCE(expr1, expr2)                                      | Null value replacement |
| **DECODE(expr, val1, res1, def)**     | CASE WHEN expr=val1 THEN res1 ELSE def END                  | Conditional expression |
| **DECODE(expr, v1, r1, v2, r2, def)** | CASE WHEN expr=v1 THEN r1 WHEN expr=v2 THEN r2 ELSE def END | Multiple conditions    |

### Mathematical Functions - Supported[​](#mathematical-functions---supported "Direct link to Mathematical Functions - Supported")

| Redshift Function | Databricks Equivalent | Notes                        |
| ----------------- | --------------------- | ---------------------------- |
| **DLOG1(x)**      | LN(x)                 | Natural logarithm            |
| **CEILING(x)**    | CEILING(x)            | Round up                     |
| **FLOOR(x)**      | FLOOR(x)              | Round down                   |
| **ROUND(x, n)**   | ROUND(x, n)           | Round to n decimal places    |
| **TRUNC(x, n)**   | TRUNC(x, n)           | Truncate to n decimal places |
| **ABS(x)**        | ABS(x)                | Absolute value               |
| **SIGN(x)**       | SIGN(x)               | Sign of number               |
| **MOD(x, y)**     | MOD(x, y)             | Modulo                       |
| **POWER(x, y)**   | POWER(x, y)           | Exponentiation               |

### Array Functions - Supported[​](#array-functions---supported "Direct link to Array Functions - Supported")

| Redshift Function                 | Databricks Equivalent   | Notes           |
| --------------------------------- | ----------------------- | --------------- |
| **ARRAY\[1,2,3]**                 | ARRAY(1,2,3)            | Array literal   |
| **ARRAY\_TO\_STRING(arr, delim)** | ARRAY\_JOIN(arr, delim) | Array to string |
| **ARRAY\_UPPER(arr, dim)**        | SIZE(arr)               | Array size      |
| **GET\_ARRAY\_LENGTH(arr)**       | SIZE(arr)               | Array length    |
| **SPLIT\_TO\_ARRAY(str, delim)**  | SPLIT(str, delim)       | String to array |

### Window Functions - Supported[​](#window-functions---supported "Direct link to Window Functions - Supported")

| Redshift Function                  | Databricks Equivalent          | Notes                      |
| ---------------------------------- | ------------------------------ | -------------------------- |
| **ROW\_NUMBER() OVER (...)**       | ROW\_NUMBER() OVER (...)       | Row number                 |
| **RANK() OVER (...)**              | RANK() OVER (...)              | Rank                       |
| **DENSE\_RANK() OVER (...)**       | DENSE\_RANK() OVER (...)       | Dense rank                 |
| **FIRST\_VALUE(col) IGNORE NULLS** | FIRST\_VALUE(col) IGNORE NULLS | First value ignoring nulls |
| **LAST\_VALUE(col) IGNORE NULLS**  | LAST\_VALUE(col) IGNORE NULLS  | Last value ignoring nulls  |
| **LAG(col, offset) OVER (...)**    | LAG(col, offset) OVER (...)    | Previous row value         |
| **LEAD(col, offset) OVER (...)**   | LEAD(col, offset) OVER (...)   | Next row value             |

### Utility Functions - Supported[​](#utility-functions---supported "Direct link to Utility Functions - Supported")

| Redshift Function                      | Databricks Equivalent               | Notes                             |
| -------------------------------------- | ----------------------------------- | --------------------------------- |
| **GENERATE\_SERIES(start, end)**       | EXPLODE(SEQUENCE(start, end, 1))    | Generate integer series           |
| **GENERATE\_SERIES(start, end, step)** | EXPLODE(SEQUENCE(start, end, step)) | Generate integer series with step |
| **URLPARSE(url, part)**                | PARSE\_URL(url, part)               | Parse URL                         |
| **PG\_BACKEND\_PID()**                 | CURRENT\_USER()                     |                                   |

***

## DDL Statements[​](#ddl-statements "Direct link to DDL Statements")

### CREATE TABLE - Supported Features[​](#create-table---supported-features "Direct link to CREATE TABLE - Supported Features")

**Redshift:**

```sql
CREATE TABLE customers (
    customer_id INT IDENTITY(1,1) PRIMARY KEY,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE,
    birth_date DATE,
    registration_date TIMESTAMP DEFAULT GETDATE(),
    loyalty_points DECIMAL(10,2) DEFAULT 0,
    status VARCHAR(20)
)
DISTSTYLE KEY
DISTKEY (customer_id)
SORTKEY (registration_date, customer_id);

```

**Converted Databricks SQL:**

```sql
CREATE OR REPLACE TABLE customers (
    customer_id INT GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 1),
    -- FIXME databricks.migration.unsupported.feature PRIMARY KEY
    first_name STRING NOT NULL,
    last_name STRING NOT NULL,
    email STRING,
    -- FIXME databricks.migration.unsupported.feature UNIQUE Constraint
    birth_date DATE,
    registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    loyalty_points NUMERIC(10,2) DEFAULT 0,
    status STRING
)
ZORDER BY (registration_date, customer_id);

```

### CREATE TABLE - Conversion Notes[​](#create-table---conversion-notes "Direct link to CREATE TABLE - Conversion Notes")

| Redshift Feature               | Databricks Conversion        | Status                                          |
| ------------------------------ | ---------------------------- | ----------------------------------------------- |
| **IDENTITY(start, increment)** | GENERATED ALWAYS AS IDENTITY | ✅ Supported                                    |
| **PRIMARY KEY**                | Commented with FIXME         | ⚠️ Not enforced (commented)                     |
| **FOREIGN KEY**                | Commented with FIXME         | ⚠️ Not enforced (commented)                     |
| **UNIQUE constraints**         | Commented with FIXME         | ⚠️ Not enforced (commented)                     |
| **CHECK constraints**          | Commented with FIXME         | ⚠️ Not enforced (commented)                     |
| **DEFAULT values**             | DEFAULT                      | ✅ Supported                                    |
| **NOT NULL**                   | NOT NULL                     | ✅ Supported                                    |
| **DISTSTYLE KEY/EVEN/ALL**     | Removed                      | ℹ️ Not applicable in Databricks                 |
| **DISTKEY (column)**           | Removed                      | ℹ️ Not applicable in Databricks                 |
| **SORTKEY (columns)**          | ZORDER BY (columns)          | ✅ Converted to Z-ordering                      |
| **COMPOUND SORTKEY**           | ZORDER BY                    | ✅ Converted to Z-ordering                      |
| **INTERLEAVED SORTKEY**        | ZORDER BY                    | ✅ Converted to Z-ordering                      |
| **ENCODE (compression)**       | Removed                      | ℹ️ Databricks handles compression automatically |

### CREATE VIEW[​](#create-view "Direct link to CREATE VIEW")

**Redshift:**

```sql
CREATE VIEW active_customers AS
SELECT
    customer_id,
    first_name || ' ' || last_name AS full_name,
    email,
    DATEDIFF(year, birth_date, CURRENT_DATE) AS age
FROM customers
WHERE status = 'active';

```

**Converted Databricks SQL:**

```sql
CREATE OR REPLACE VIEW active_customers AS
SELECT
    customer_id,
    first_name || ' ' || last_name AS full_name,
    email,
    YEAR(CURRENT_DATE) - YEAR(birth_date) AS age
FROM customers
WHERE status = 'active';

```

### CREATE TABLE AS SELECT (CTAS)[​](#create-table-as-select-ctas "Direct link to CREATE TABLE AS SELECT (CTAS)")

**Redshift:**

```sql
CREATE TABLE customer_summary AS
SELECT
    customer_id,
    COUNT(*) AS order_count,
    SUM(total_amount) AS total_spent
FROM orders
GROUP BY customer_id;

```

**Converted Databricks SQL:**

```sql
CREATE OR REPLACE TABLE customer_summary AS
SELECT
    customer_id,
    COUNT(*) AS order_count,
    SUM(total_amount) AS total_spent
FROM orders
GROUP BY customer_id;

```

### CREATE TABLE LIKE[​](#create-table-like "Direct link to CREATE TABLE LIKE")

**Redshift:**

```sql
CREATE TABLE customers_backup (LIKE customers INCLUDING DEFAULTS);

```

**Converted Databricks SQL:**

```sql
CREATE OR REPLACE TABLE customers_backup AS
SELECT * FROM customers;

```

***

## DML Statements[​](#dml-statements "Direct link to DML Statements")

### UPDATE with JOIN - Converted to MERGE[​](#update-with-join---converted-to-merge "Direct link to UPDATE with JOIN - Converted to MERGE")

**Redshift:**

```sql
WITH customer_segments AS (
    SELECT
        customer_id,
        UPPER(TRIM(first_name)) || ' ' || UPPER(TRIM(last_name)) AS full_name,
        DATEDIFF(year, date_of_birth, CURRENT_DATE) AS age,
        CASE
            WHEN DATEDIFF(year, date_of_birth, CURRENT_DATE) < 25 THEN 'Young'
            WHEN DATEDIFF(year, date_of_birth, CURRENT_DATE) BETWEEN 25 AND 45 THEN 'Adult'
            ELSE 'Senior'
        END AS age_segment,
        DATE_PART(month, registration_date) AS registration_month
    FROM customers
    WHERE status = 'active'
)
UPDATE customers
SET
    first_name = cs.full_name,
    updated_at = CURRENT_TIMESTAMP
FROM customer_segments cs
WHERE customers.customer_id = cs.customer_id
    AND cs.age_segment = 'Senior'
    AND cs.registration_month IN (1, 12);

```

**Converted Databricks SQL:**

```sql
WITH customer_segments AS (
    SELECT
        customer_id,
        UPPER(TRIM(first_name)) || ' ' || UPPER(TRIM(last_name)) AS full_name,
        YEAR(CURRENT_DATE) - YEAR(date_of_birth) AS age,
        CASE
            WHEN YEAR(CURRENT_DATE) - YEAR(date_of_birth) < 25 THEN 'Young'
            WHEN YEAR(CURRENT_DATE) - YEAR(date_of_birth) BETWEEN 25 AND 45 THEN 'Adult'
            ELSE 'Senior'
        END AS age_segment,
        DATE_PART(month, registration_date) AS registration_month
    FROM customers
    WHERE status = 'active'
)
MERGE INTO customers
USING customer_segments cs
ON customers.customer_id = cs.customer_id
   AND cs.age_segment = 'Senior'
   AND cs.registration_month IN (1, 12)
WHEN MATCHED THEN UPDATE SET
    first_name = cs.full_name,
    updated_at = CURRENT_TIMESTAMP;

```

### DELETE with JOIN - Converted to MERGE[​](#delete-with-join---converted-to-merge "Direct link to DELETE with JOIN - Converted to MERGE")

**Redshift:**

```sql
DELETE FROM orders
USING customers
WHERE orders.customer_id = customers.customer_id
    AND customers.status = 'inactive'
    AND orders.order_date < CURRENT_DATE - 365;

```

**Converted Databricks SQL:**

```sql
MERGE INTO orders
USING customers
ON orders.customer_id = customers.customer_id
   AND customers.status = 'inactive'
   AND orders.order_date < CURRENT_DATE - INTERVAL 365 DAYS
WHEN MATCHED THEN DELETE;

```

### INSERT INTO SELECT[​](#insert-into-select "Direct link to INSERT INTO SELECT")

**Redshift:**

```sql
INSERT INTO customer_archive
SELECT * FROM customers
WHERE status = 'deleted'
    AND updated_at < CURRENT_DATE - 730;

```

**Converted Databricks SQL:**

```sql
INSERT INTO customer_archive
SELECT * FROM customers
WHERE status = 'deleted'
    AND updated_at < DATE_ADD(current_date, -730);

```

***

## Verified Conversion Examples[​](#verified-conversion-examples "Direct link to Verified Conversion Examples")

The following examples are taken directly from the functional test suite and show actual input/output conversions.

### Example 1: Date Functions[​](#example-1-date-functions "Direct link to Example 1: Date Functions")

**Source Redshift SQL:**

```sql
SELECT
    o.order_number,
    CONCAT(c.first_name, ' ', c.last_name) AS customer_name,
    TO_CHAR(o.order_date, 'YYYY-MM-DD') AS formatted_order_date,
    DATEDIFF(day, o.order_date, o.estimated_delivery) AS delivery_days
FROM orders o
INNER JOIN customers c ON o.customer_id = c.customer_id
WHERE o.order_date >= DATEADD(month, -1, CURRENT_DATE)
ORDER BY o.order_date DESC;

```

**Converted Databricks SQL:**

```sql
SELECT
    o.order_number,
    CONCAT(c.first_name, ' ', c.last_name) AS customer_name,
    DATE_FORMAT(o.order_date,'y-MM-dd') AS formatted_order_date,
    DATEDIFF(DAY, o.order_date, o.estimated_delivery) AS delivery_days
FROM orders o
INNER JOIN customers c ON o.customer_id = c.customer_id
WHERE o.order_date >= ADD_MONTHS(CURRENT_DATE, -1)
ORDER BY o.order_date DESC;

```

**Key Conversions:**

* `TO_CHAR(date, format)` → `DATE_FORMAT(date, format)` with format translation
* `DATEDIFF(day, ...)` → `DATEDIFF(DAY, ...)`
* `DATEADD(month, n, date)` → `ADD_MONTHS(date, n)`

***

### Example 2: UPDATE with FROM → MERGE[​](#example-2-update-with-from--merge "Direct link to Example 2: UPDATE with FROM → MERGE")

**Source Redshift SQL:**

```sql
UPDATE customers
SET last_login = CURRENT_TIMESTAMP,
    updated_at = CURRENT_TIMESTAMP
WHERE UPPER(email) LIKE '%@GMAIL.COM'
AND last_login < DATEADD(month, -3, CURRENT_DATE);

```

**Converted Databricks SQL:**

```sql
UPDATE customers
SET last_login = CURRENT_TIMESTAMP,
    updated_at = CURRENT_TIMESTAMP
WHERE UPPER(email) LIKE '%@GMAIL.COM'
AND last_login < ADD_MONTHS(CURRENT_DATE, -3);

```

**Key Conversions:**

* `DATEADD(month, -3, date)` → `ADD_MONTHS(date, -3)`
* Simple UPDATE statements remain as UPDATE (no MERGE needed)

***

### Example 3: CTEs with UPDATE FROM → MERGE[​](#example-3-ctes-with-update-from--merge "Direct link to Example 3: CTEs with UPDATE FROM → MERGE")

**Source Redshift SQL:**

```sql
WITH recent_orders AS (
    SELECT
        o.customer_id,
        COUNT(*) AS order_count,
        SUM(o.total_amount) AS total_spent,
        MAX(o.order_date) AS last_order_date,
        SUBSTRING(o.order_number, 1, 4) AS order_prefix
    FROM orders o
    WHERE o.order_date >= DATEADD(month, -6, CURRENT_DATE)
        AND o.status IN ('completed', 'shipped')
    GROUP BY o.customer_id, SUBSTRING(o.order_number, 1, 4)
    HAVING COUNT(*) >= 3
),
loyalty_updates AS (
    SELECT
        ro.customer_id,
        FLOOR(ro.total_spent / 100) * 10 AS bonus_points,
        CONCAT('VIP-', LPAD(ro.customer_id::VARCHAR, 6, '0')) AS new_customer_code
    FROM recent_orders ro
)
UPDATE customers
SET
    loyalty_points = loyalty_points + lu.bonus_points,
    customer_code = lu.new_customer_code,
    updated_at = CURRENT_TIMESTAMP
FROM loyalty_updates lu
WHERE customers.customer_id = lu.customer_id;

```

**Converted Databricks SQL:**

```sql
WITH recent_orders AS (
    SELECT
        o.customer_id,
        COUNT(*) AS order_count,
        SUM(o.total_amount) AS total_spent,
        MAX(o.order_date) AS last_order_date,
        SUBSTRING(o.order_number, 1, 4) AS order_prefix
    FROM orders o
    WHERE o.order_date >= ADD_MONTHS(CURRENT_DATE, -6)
        AND o.status IN ('completed', 'shipped')
    GROUP BY o.customer_id, SUBSTRING(o.order_number, 1, 4)
    HAVING COUNT(*) >= 3
),
loyalty_updates AS (
    SELECT
        ro.customer_id,
        FLOOR(ro.total_spent / 100) * 10 AS bonus_points,
        CONCAT('VIP-', LPAD(ro.customer_id::STRING, 6, '0')) AS new_customer_code
    FROM recent_orders ro
)
MERGE INTO customers
USING loyalty_updates lu
ON customers.customer_id = lu.customer_id
WHEN MATCHED THEN UPDATE SET
    loyalty_points = loyalty_points + lu.bonus_points,
    customer_code = lu.new_customer_code,
    updated_at = CURRENT_TIMESTAMP;

```

**Key Conversions:**

* `UPDATE ... FROM ...` → `MERGE INTO ... USING ... WHEN MATCHED`
* `::VARCHAR` → `::STRING`
* `DATEADD(month, -6, date)` → `ADD_MONTHS(date, -6)`
* CTEs preserved with updated syntax

***

### Example 4: REGEXP\_COUNT Function[​](#example-4-regexp_count-function "Direct link to Example 4: REGEXP_COUNT Function")

**Source Redshift SQL:**

```sql
UPDATE customers
SET loyalty_points = loyalty_points +
    CASE
        WHEN REGEXP_COUNT(email, '[A-Z]') > 3 THEN 100
        WHEN REGEXP_COUNT(email, '[0-9]') > 2 THEN 50
        ELSE 25
    END,
    status = DECODE(
        REGEXP_COUNT(phone, '[0-9]'),
        10, 'VERIFIED',
        11, 'VERIFIED',
        'PENDING'
    )
WHERE customer_id IN (
    SELECT DISTINCT o.customer_id
    FROM orders o
    JOIN order_items oi ON o.order_id = oi.order_id
    WHERE o.order_date >= CURRENT_DATE - INTERVAL '30 days'
    AND o.total_amount > 500
);

```

**Converted Databricks SQL:**

```sql
UPDATE customers
SET loyalty_points = loyalty_points +
    CASE
        WHEN LENGTH(email) - LENGTH(REGEXP_REPLACE(email, '[A-Z]', '')) > 3 THEN 100
        WHEN LENGTH(email) - LENGTH(REGEXP_REPLACE(email, '[0-9]', '')) > 2 THEN 50
        ELSE 25
    END,
    status = case when LENGTH(phone) - LENGTH(REGEXP_REPLACE(phone, '[0-9]', ''))=10 then 'VERIFIED' when LENGTH(phone) - LENGTH(REGEXP_REPLACE(phone, '[0-9]', ''))=11 then 'VERIFIED' else 'PENDING' END
WHERE customer_id IN (
    SELECT DISTINCT o.customer_id
    FROM orders o
    JOIN order_items oi ON o.order_id = oi.order_id
    WHERE o.order_date >= CURRENT_DATE - INTERVAL '30 days'
    AND o.total_amount > 500
);

```

**Key Conversions:**

* `REGEXP_COUNT(str, pattern)` → `LENGTH(str) - LENGTH(REGEXP_REPLACE(str, pattern, ''))`
* `DECODE(expr, v1, r1, v2, r2, default)` → `CASE WHEN expr=v1 THEN r1 WHEN expr=v2 THEN r2 ELSE default END`

***

### Example 5: INSERT with REGEXP\_COUNT and Complex Functions[​](#example-5-insert-with-regexp_count-and-complex-functions "Direct link to Example 5: INSERT with REGEXP_COUNT and Complex Functions")

**Source Redshift SQL:**

```sql
INSERT INTO addresses (customer_id, address_type, street_address, city, state_province, postal_code, country, is_primary, created_at)
SELECT
    c.customer_id,
    'BILLING',
    TRANSLATE(c.first_name || ' ' || c.last_name, 'AEIOU', '12345') || ' Street',
    CASE
        WHEN REGEXP_COUNT(c.email, '@gmail') > 0 THEN 'New York'
        WHEN REGEXP_COUNT(c.email, '@yahoo') > 0 THEN 'Los Angeles'
        ELSE 'Chicago'
    END,
    DECODE(
        REGEXP_COUNT(c.phone, '^1'),
        1, 'NY',
        'CA'
    ),
    LPAD(ABS(RANDOM() * 99999)::INT, 5, '0'),
    'USA',
    1,
    CURRENT_TIMESTAMP
FROM customers c
WHERE c.customer_id NOT IN (
    SELECT DISTINCT customer_id
    FROM addresses
    WHERE address_type = 'BILLING'
)
AND c.registration_date >= CURRENT_DATE - INTERVAL '90 days';

```

**Converted Databricks SQL:**

```sql
INSERT INTO addresses (customer_id, address_type, street_address, city, state_province, postal_code, country, is_primary, created_at)
SELECT
    c.customer_id,
    'BILLING',
    TRANSLATE(c.first_name || ' ' || c.last_name, 'AEIOU', '12345') || ' Street',
    CASE
        WHEN LENGTH(c.email) - LENGTH(REGEXP_REPLACE(c.email, '@gmail', '')) > 0 THEN 'New York'
        WHEN LENGTH(c.email) - LENGTH(REGEXP_REPLACE(c.email, '@yahoo', '')) > 0 THEN 'Los Angeles'
        ELSE 'Chicago'
    END,
    case when LENGTH(c.phone) - LENGTH(REGEXP_REPLACE(c.phone, '^1', ''))=1 then 'NY' else 'CA' END,
    LPAD(ABS(RANDOM() * 99999)::INT, 5, '0'),
    'USA',
    1,
    CURRENT_TIMESTAMP
FROM customers c
WHERE c.customer_id NOT IN (
    SELECT DISTINCT customer_id
    FROM addresses
    WHERE address_type = 'BILLING'
)
AND c.registration_date >= CURRENT_DATE - INTERVAL '90 days';

```

**Key Conversions:**

* Multiple `REGEXP_COUNT()` conversions in CASE statements
* `DECODE()` with 3 parameters → inline CASE
* `TRANSLATE()` function preserved (supported in Databricks)
* `LPAD()`, `ABS()`, `RANDOM()` preserved

***

### Example 6: DELETE with CTEs[​](#example-6-delete-with-ctes "Direct link to Example 6: DELETE with CTEs")

**Source Redshift SQL:**

```sql
WITH expired_products AS (
    SELECT
        p.product_id,
        p.product_name,
        REPLACE(LOWER(p.product_name), ' ', '_') AS slug_name,
        DATEDIFF(day, p.launch_date, CURRENT_DATE) AS days_since_launch,
        DATE_TRUNC('quarter', p.launch_date) AS launch_quarter
    FROM products p
    WHERE p.launch_date < DATEADD(year, -2, CURRENT_DATE)
        AND p.stock_quantity = 0
        AND p.is_active = 1
),
category_info AS (
    SELECT
        c.category_id,
        INITCAP(c.category_name) AS formatted_name
    FROM categories c
    WHERE c.is_active = 1
)
DELETE FROM products
WHERE product_id IN (
    SELECT ep.product_id
    FROM expired_products ep
    JOIN category_info ci ON ep.product_id IN (
        SELECT product_id FROM products WHERE category_id = ci.category_id
    )
    WHERE ep.days_since_launch > 730
        AND CHARINDEX('discontinued', LOWER(ep.product_name)) > 0
);

```

**Converted Databricks SQL:**

```sql
WITH expired_products AS (
    SELECT
        p.product_id,
        p.product_name,
        REPLACE(LOWER(p.product_name), ' ', '_') AS slug_name,
        DATEDIFF(DAY, p.launch_date, CURRENT_DATE) AS days_since_launch,
        DATE_TRUNC('quarter', p.launch_date) AS launch_quarter
    FROM products p
    WHERE p.launch_date < DATEADD(year, -2, CURRENT_DATE)
        AND p.stock_quantity = 0
        AND p.is_active = 1
),
category_info AS (
    SELECT
        c.category_id,
        INITCAP(c.category_name) AS formatted_name
    FROM categories c
    WHERE c.is_active = 1
)
DELETE FROM products
WHERE product_id IN (
    SELECT ep.product_id
    FROM expired_products ep
    JOIN category_info ci ON ep.product_id IN (
        SELECT product_id FROM products WHERE category_id = ci.category_id
    )
    WHERE ep.days_since_launch > 730
        AND INSTR(LOWER(ep.product_name), 'discontinued') > 0
);

```

**Key Conversions:**

* `DATEDIFF(day, ...)` → `DATEDIFF(DAY, ...)`
* `CHARINDEX(substr, str)` → `INSTR(str, substr)` (note: argument order swapped)
* `INITCAP()` preserved (supported in Databricks)
* `DATE_TRUNC()` preserved
* CTEs with DELETE preserved

***

## Advanced Features[​](#advanced-features "Direct link to Advanced Features")

### Common Table Expressions (CTEs)[​](#common-table-expressions-ctes "Direct link to Common Table Expressions (CTEs)")

**Redshift:**

```sql
WITH monthly_sales AS (
    SELECT
        DATE_TRUNC('month', order_date) AS month,
        SUM(total_amount) AS monthly_total
    FROM orders
    GROUP BY 1
),
customer_orders AS (
    SELECT
        customer_id,
        COUNT(*) AS order_count,
        AVG(total_amount) AS avg_order_value
    FROM orders
    GROUP BY customer_id
)
SELECT
    ms.month,
    ms.monthly_total,
    COUNT(DISTINCT co.customer_id) AS active_customers,
    AVG(co.avg_order_value) AS avg_customer_value
FROM monthly_sales ms
CROSS JOIN customer_orders co
GROUP BY ms.month, ms.monthly_total;

```

**Converted Databricks SQL:**

```sql
WITH monthly_sales AS (
    SELECT
        DATE_TRUNC('month', order_date) AS month,
        SUM(total_amount) AS monthly_total
    FROM orders
    GROUP BY 1
),
customer_orders AS (
    SELECT
        customer_id,
        COUNT(*) AS order_count,
        AVG(total_amount) AS avg_order_value
    FROM orders
    GROUP BY customer_id
)
SELECT
    ms.month,
    ms.monthly_total,
    COUNT(DISTINCT co.customer_id) AS active_customers,
    AVG(co.avg_customer_value) AS avg_customer_value
FROM monthly_sales ms
CROSS JOIN customer_orders co
GROUP BY ms.month, ms.monthly_total;

```

### Window Functions with IGNORE NULLS[​](#window-functions-with-ignore-nulls "Direct link to Window Functions with IGNORE NULLS")

**Redshift:**

```sql
SELECT
    customer_id,
    order_date,
    total_amount,
    FIRST_VALUE(payment_method) IGNORE NULLS
        OVER (PARTITION BY customer_id ORDER BY order_date) AS first_payment_method,
    LAST_VALUE(payment_method) IGNORE NULLS
        OVER (PARTITION BY customer_id ORDER BY order_date
              ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS last_payment_method
FROM orders;

```

**Converted Databricks SQL:**

```sql
SELECT
    customer_id,
    order_date,
    total_amount,
    FIRST_VALUE(payment_method) IGNORE NULLS
        OVER (PARTITION BY customer_id ORDER BY order_date) AS first_payment_method,
    LAST_VALUE(payment_method) IGNORE NULLS
        OVER (PARTITION BY customer_id ORDER BY order_date
              ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS last_payment_method
FROM orders;

```

### LISTAGG with DISTINCT[​](#listagg-with-distinct "Direct link to LISTAGG with DISTINCT")

**Redshift:**

```sql
SELECT
    customer_id,
    LISTAGG(DISTINCT product_category, ', ')
        WITHIN GROUP (ORDER BY product_category) AS categories
FROM order_items
GROUP BY customer_id;

```

**Converted Databricks SQL:**

```sql
SELECT
    customer_id,
    ARRAY_JOIN(
        ARRAY_DISTINCT(
            SPLIT(
                LISTAGG(product_category, ', ')
                    WITHIN GROUP (ORDER BY product_category),
                ', '
            )
        ),
        ', '
    ) AS categories
FROM order_items
GROUP BY customer_id;

```

### Timezone Conversions[​](#timezone-conversions "Direct link to Timezone Conversions")

**Redshift:**

```sql
SELECT
    order_id,
    order_timestamp,
    order_timestamp AT TIME ZONE 'America/New_York' AS ny_time,
    CONVERT_TIMEZONE('America/New_York', order_timestamp) AS ny_converted,
    CONVERT_TIMEZONE('UTC', 'America/Los_Angeles', order_timestamp) AS la_time
FROM orders;

```

**Converted Databricks SQL:**

```sql
SELECT
    order_id,
    order_timestamp,
    FROM_UTC_TIMESTAMP(order_timestamp, 'America/New_York') AS ny_time,
    FROM_UTC_TIMESTAMP(order_timestamp, 'America/New_York') AS ny_converted,
    FROM_UTC_TIMESTAMP(TO_UTC_TIMESTAMP(order_timestamp, 'UTC'), 'America/Los_Angeles') AS la_time
FROM orders;

```

### Lateral Column References[​](#lateral-column-references "Direct link to Lateral Column References")

Redshift allows referencing previously defined columns in the same SELECT clause. Databricks conversion handles this automatically.

**Redshift:**

```sql
SELECT
    customer_id,
    first_name || ' ' || last_name AS full_name,
    LENGTH(full_name) AS name_length,  -- Lateral reference to full_name
    UPPER(full_name) AS full_name_upper  -- Another lateral reference
FROM customers;

```

**Converted Databricks SQL:**

```sql
SELECT
    customer_id,
    first_name || ' ' || last_name AS full_name,
    LENGTH(first_name || ' ' || last_name) AS name_length,
    UPPER(first_name || ' ' || last_name) AS full_name_upper
FROM customers;

```

***

## Redshift-Specific Features[​](#redshift-specific-features "Direct link to Redshift-Specific Features")

### Distribution Styles[​](#distribution-styles "Direct link to Distribution Styles")

Redshift distribution styles (DISTSTYLE, DISTKEY) are removed during conversion as Databricks handles data distribution automatically.

**Redshift:**

```sql
CREATE TABLE orders (
    order_id INT,
    customer_id INT,
    order_date DATE,
    total_amount DECIMAL(10,2)
)
DISTSTYLE KEY
DISTKEY (customer_id);

```

**Converted Databricks SQL:**

```sql
CREATE OR REPLACE TABLE orders (
    order_id INT,
    customer_id INT,
    order_date DATE,
    total_amount NUMERIC(10,2)
);

```

### Sort Keys[​](#sort-keys "Direct link to Sort Keys")

Redshift SORTKEY is converted to Databricks ZORDER BY for optimized query performance.

**Redshift:**

```sql
CREATE TABLE events (
    event_id BIGINT,
    event_date DATE,
    user_id INT,
    event_type VARCHAR(50)
)
COMPOUND SORTKEY (event_date, user_id);

```

**Converted Databricks SQL:**

```sql
CREATE OR REPLACE TABLE events (
    event_id BIGINT,
    event_date DATE,
    user_id INT,
    event_type STRING
)
ZORDER BY (event_date, user_id);

```

### Compression Encoding[​](#compression-encoding "Direct link to Compression Encoding")

Redshift ENCODE clauses for column compression are removed as Databricks manages compression automatically.

**Redshift:**

```sql
CREATE TABLE products (
    product_id INT ENCODE az64,
    product_name VARCHAR(200) ENCODE lzo,
    description TEXT ENCODE zstd,
    price DECIMAL(10,2) ENCODE raw
);

```

**Converted Databricks SQL:**

```sql
CREATE OR REPLACE TABLE products (
    product_id INT,
    product_name STRING,
    description STRING,
    price NUMERIC(10,2)
);

```

### Vacuum and Analyze[​](#vacuum-and-analyze "Direct link to Vacuum and Analyze")

Redshift VACUUM and ANALYZE commands are removed as Databricks handles optimization automatically.

**Redshift:**

```sql
VACUUM orders;
ANALYZE customers;
VACUUM FULL products;

```

**Converted Databricks SQL:**

```sql
-- VACUUM and ANALYZE commands are not needed in Databricks
-- Databricks performs automatic optimization

```

***

## Known Limitations[​](#known-limitations "Direct link to Known Limitations")

### Features Not Supported[​](#features-not-supported "Direct link to Features Not Supported")

1. **SUPER Data Type**

   * Redshift's semi-structured SUPER type
   * **Workaround**: Use STRING type and parse with JSON functions, or use VARIANT type

2. **HLLSKETCH Data Type**

   * HyperLogLog sketches for approximate counting
   * **Workaround**: Use `APPROX_COUNT_DISTINCT()` or custom UDFs

3. **GEOMETRY Data Type**

   * Spatial/geographic data
   * **Workaround**: Store as STRING and use Databricks spatial functions

4. **Referential Integrity Constraints**

   * PRIMARY KEY, FOREIGN KEY constraints are commented out
   * **Workaround**: Implement validation logic in application or ETL code

5. **CHECK Constraints**

   * Column-level CHECK constraints are commented out
   * **Workaround**: Implement validation in application code or use Delta table constraints where applicable

6. **UNIQUE Constraints**

   * UNIQUE constraints are commented out
   * **Workaround**: Use MERGE operations or application-level validation

7. **Cursor Operations**

   * DECLARE CURSOR, OPEN, FETCH, CLOSE
   * **Workaround**: Rewrite using set-based operations or Databricks SQL stored procedures

8. **UNLOAD Command**

   * Redshift UNLOAD to S3
   * **Workaround**: Use Databricks `COPY INTO` or DataFrame write operations

9. **COPY Command**

   * Redshift COPY from S3/files
   * **Workaround**: Use Databricks `COPY INTO` or DataFrame read operations

10. **Redshift Spectrum External Tables**

    * Querying data directly in S3
    * **Workaround**: Use Databricks External Tables or Delta Lake

***

## Next Steps[​](#next-steps "Direct link to Next Steps")

1. **Export Redshift SQL scripts** to .sql files
2. **Run conversion** using Lakebridge CLI:
   <!-- -->
   ```bash
   databricks labs lakebridge transpile \
     --source-dialect redshift \
     --input-source /path/to/redshift/scripts \
     --output-folder /output/databricks-sql \
     --target-technology databricks-sql

   ```
3. **Review generated SQL** for FIXME comments
4. **Address unsupported features** (constraints, SUPER types, etc.)
5. **Test converted SQL** in Databricks workspace
6. **Optimize with Delta Lake features** (OPTIMIZE, Z-ORDER)
7. **Deploy to production**

For more information, see:

* [Source Systems Overview](/lakebridge/docs/transpile/source_systems.md)
* [BladeBridge Configuration](/lakebridge/docs/transpile/pluggable_transpilers/bladebridge/bladebridge_configuration.md)
* [Transpile CLI Reference](/lakebridge/docs/transpile.md)
