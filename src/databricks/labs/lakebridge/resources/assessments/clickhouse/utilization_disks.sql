-- Storage disks (system.disks).
SELECT
    name,
    path,
    free_space,
    total_space,
    keep_free_space,
    type
FROM system.disks
