WITH undo_ts AS (
    SELECT con_id, tablespace_name
    FROM cdb_tablespaces
    WHERE contents = 'UNDO'
),
     sub AS (
         SELECT c.name AS con_name,
                f.tablespace_name,
                f.con_id,
                f.bytes/1024/1024/1024 AS gb,
                NVL(t.free_bytes, 0)/1024/1024/1024 AS freegb,
                f.maxbytes/1024/1024/1024 AS maxgb
         FROM cdb_data_files f
                  LEFT JOIN (
             SELECT con_id, tablespace_name, SUM(bytes) AS free_bytes
             FROM cdb_free_space
             GROUP BY con_id, tablespace_name
         ) t ON t.con_id = f.con_id AND t.tablespace_name = f.tablespace_name
                  JOIN (SELECT DISTINCT con_id, name FROM gv$containers) c
                       ON c.con_id = f.con_id
     )
SELECT con_name,
       CASE
           WHEN tablespace_name IN ('SYSTEM','SYSAUX') THEN 'SYSTEM'
           WHEN tablespace_name IN (SELECT tablespace_name FROM undo_ts WHERE con_id = sub.con_id) THEN 'UNDO'
           ELSE 'USER_DATA'
           END AS tablespace_type,
       SUM(gb) AS gb,
       SUM(freegb) AS freegb,
       SUM(maxgb) AS maxgb
FROM sub
GROUP BY con_name,
         CASE
             WHEN tablespace_name IN ('SYSTEM','SYSAUX') THEN 'SYSTEM'
             WHEN tablespace_name IN (SELECT tablespace_name FROM undo_ts WHERE con_id = sub.con_id) THEN 'UNDO'
             ELSE 'USER_DATA'
             END
ORDER BY 1
