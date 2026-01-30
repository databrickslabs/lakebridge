/**
 * Retrieves storage and row count statistics for all user tables in the specified
 * database by querying sys.dm_db_partition_stats and sys.objects. Returns table
 * name, total rows, reserved, used, and unused space (MB), breakdown of data vs.
 * index space, and a timestamp indicating when the data was extracted.
 */
SELECT o.NAME                                                               AS
       TableName,
       Sum(ps.row_count)                                                    AS
       [RowCount],
       Sum(ps.reserved_page_count) * 8 / 1024                               AS
       ReservedMB,
       Sum(ps.used_page_count) * 8 / 1024                                   AS
       UsedMB,
       ( Sum(ps.reserved_page_count) - Sum(ps.used_page_count) ) * 8 / 1024 AS
       UnusedMB,
       Sum(CASE
             WHEN ps.index_id < 2 THEN ps.in_row_data_page_count
                                       + ps.lob_used_page_count
                                       + ps.row_overflow_used_page_count
             ELSE 0
           END) * 8 / 1024                                                  AS
       DataMB,
       Sum(CASE
             WHEN ps.index_id >= 2 THEN ps.in_row_data_page_count
             ELSE 0
           END) * 8 / 1024                                                  AS
       IndexMB,
       Sysdatetime()                                                        AS
       extract_ts
FROM   sys.dm_db_partition_stats AS ps
       JOIN sys.objects AS o
         ON ps.object_id = o.object_id
WHERE  o.type = 'U'
GROUP  BY Schema_name(o.schema_id),
          o.NAME;
