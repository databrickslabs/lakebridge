/**
 * Retrieves metadata for all indexed views in the specified database by joining
 * `sys.views` with `sys.indexes`. Returns view details for those with a clustered
 * index (index_id = 1) along with a timestamp indicating when the data was extracted.
 */
SELECT v.[name]      AS indexed_view_name,
       s.[name]      AS schema_name,
       i.[name]      AS index_name,
       i.[type_desc] AS index_type,
       i.[index_id],
       Sysdatetime() AS extract_ts
FROM   sys.views AS v
       JOIN sys.schemas AS s
         ON v.[schema_id] = s.[schema_id]
       JOIN sys.indexes AS i
         ON v.[object_id] = i.[object_id]
WHERE  i.[index_id] = 1;
