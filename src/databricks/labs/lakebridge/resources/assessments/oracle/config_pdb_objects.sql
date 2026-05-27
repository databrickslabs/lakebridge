SELECT cont.NAME as PDB_NAME,OWNER,OBJECT_TYPE,COUNT(*) as CNT
FROM CDB_OBJECTS o,
     (select distinct con_id as con_id, name from gv$containers) cont
WHERE o.CON_ID=cont.con_id
AND OWNER in (select username from cdb_users where oracle_maintained='N' and cont.con_id=con_id)
GROUP BY cont.name,OWNER,OBJECT_TYPE
ORDER BY 1,2
