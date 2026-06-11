SELECT TO_CHAR(mtime,'YYYY/MM/DD') AS mtime,
       pdb_name,
       instance_number,
       TO_CHAR(mtime,'HH24') AS hour,
       core_nb,
       LOAD AS value
FROM
    (SELECT to_date(mtime,'YYYY-MM-DD HH24') mtime,
    cont.name AS pdb_name,
    instance_number,
    core_nb,
    ROUND(SUM(load),2) LOAD
    FROM
    (SELECT ash.instance_number,
    ash.con_id,
    TO_CHAR(sample_time,'YYYY-MM-DD HH24') mtime,
    cpu.core_nb,
    COUNT(*)/360/cpu.core_nb load
    FROM cdb_hist_active_sess_history ash,
    (SELECT inst_id, to_number(value) AS core_nb
    FROM gv$osstat
    WHERE stat_name='NUM_CPUS') cpu
    WHERE ash.instance_number = cpu.inst_id
    GROUP BY ash.instance_number, ash.con_id, cpu.core_nb,
    TO_CHAR(sample_time,'YYYY-MM-DD HH24'),
    session_state
    ) q,
    (SELECT DISTINCT con_id, name FROM gv$containers) cont
    WHERE cont.con_id = q.con_id
    GROUP BY cont.name, mtime, instance_number, core_nb
    )
ORDER BY 1,2,3
