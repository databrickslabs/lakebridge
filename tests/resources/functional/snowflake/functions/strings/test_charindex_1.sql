
-- snowflake sql:
select charindex('an', 'banana', 3),
                charindex('ab', 'abababab'), n, h, CHARINDEX(n, h) FROM pos;

-- databricks sql:
SELECT LOCATE('an', 'banana', 3), LOCATE('ab', 'abababab'), n, h, LOCATE(n, h) FROM pos;
