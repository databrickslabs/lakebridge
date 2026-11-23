-- In-flight merges (system.merges).
SELECT
    database,
    table,
    elapsed,
    progress,
    num_parts,
    result_part_name,
    total_size_bytes_compressed,
    bytes_read_uncompressed,
    rows_read,
    bytes_written_uncompressed,
    rows_written,
    memory_usage,
    is_mutation
FROM system.merges
