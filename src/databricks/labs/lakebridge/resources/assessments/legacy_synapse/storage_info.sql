SELECT
    pdw_node_id AS node_id,
    (SUM(reserved_page_count) * 8) / 1024 AS ReservedSpaceMB,
    (SUM(used_page_count) * 8) / 1024 AS UsedSpaceMB,
    CURRENT_TIMESTAMP AS extract_ts
FROM sys.dm_pdw_nodes_db_partition_stats
GROUP BY pdw_node_id
