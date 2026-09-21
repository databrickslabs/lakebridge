SELECT
    pdw_node_id AS node_id,
    CAST((SUM(reserved_page_count) * 8.0) / 1024.0 AS FLOAT) AS reserved_space_mb,
    CAST((SUM(used_page_count) * 8.0) / 1024.0 AS FLOAT) AS used_space_mb,
    CURRENT_TIMESTAMP AS extract_ts
FROM sys.dm_pdw_nodes_db_partition_stats
GROUP BY pdw_node_id
