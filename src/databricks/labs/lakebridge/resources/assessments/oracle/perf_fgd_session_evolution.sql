SELECT con.name,
       sh.instance_number,
       u.username,
       TRUNC(sh.sample_time, 'MI') AS snap_time,
       COUNT(DISTINCT sh.session_id || ',' || sh.session_serial#) AS foregd_session_cnt
FROM cdb_hist_active_sess_history sh
         JOIN (
    SELECT con_id, name
    FROM v$containers
    WHERE name != 'PDB$SEED'
) con ON con.con_id = sh.con_id
         JOIN (
    SELECT user_id, username
    FROM cdb_users
    WHERE oracle_maintained = 'N'
) u ON u.user_id = sh.user_id
WHERE sh.session_type = 'FOREGROUND'
GROUP BY con.name,
         sh.instance_number,
         TRUNC(sh.sample_time, 'MI'),
         u.username
ORDER BY 1, 4, 2
