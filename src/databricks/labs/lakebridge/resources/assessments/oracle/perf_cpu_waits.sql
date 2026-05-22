SELECT cont.name AS pdb_name,
       ash.instance_number,
       ash.mtime,
       ash.event,
       ash.wait_class,
       ash.total_wait_time
FROM (
         SELECT instance_number,
                con_id,
                con_dbid,
                TRUNC(sample_time, 'HH24') AS mtime,
                NVL(event, 'ON CPU') AS event,
                NVL(wait_class, 'ON CPU') AS wait_class,
                COUNT(*) * 10 AS total_wait_time
         FROM cdb_hist_active_sess_history
         GROUP BY instance_number, con_id, con_dbid,
                  TRUNC(sample_time, 'HH24'), event, wait_class
     ) ash
         JOIN (SELECT DISTINCT con_id, name, dbid FROM gv$containers) cont
              ON cont.con_id = ash.con_id
                  AND cont.dbid = ash.con_dbid
ORDER BY pdb_name, mtime
