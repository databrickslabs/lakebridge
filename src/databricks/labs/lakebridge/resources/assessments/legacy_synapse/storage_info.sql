SELECT
    pdw_node_id AS node_id,
    (SUM(reserved_page_count) * 8.0) / 1024.0 AS reserved_space_mb,
    (SUM(used_page_count) * 8.0) / 1024.0 AS used_space_mb,
    CURRENT_TIMESTAMP AS extract_ts
FROM sys.dm_pdw_nodes_db_partition_stats
GROUP BY pdw_node_id
