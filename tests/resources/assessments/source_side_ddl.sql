-- Exercises the source_ddl step type: a statement with no result set
-- (cursor.description is None). Temp tables need no extra permissions.
CREATE TABLE #lakebridge_source_ddl_check (id INT);
DROP TABLE #lakebridge_source_ddl_check;
