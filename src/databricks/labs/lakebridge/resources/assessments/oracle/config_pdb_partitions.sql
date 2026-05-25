-- Has to be executed on CDB
SELECT cont.NAME as PDB_NAME,OWNER,OBJECT_TYPE,CNT
from
(
    select owner,con_id,'TABLE (NON PARTITIONED)' as OBJECT_TYPE,count(*) as cnt from cdb_tables where partitioned='NO' group by owner,con_id
    union
    select owner,con_id,'TABLE (PARTITIONED)',count(*) as cnt from cdb_tables where partitioned='YES' group by owner,con_id
    union
    select owner,con_id,'INDEX (NON PARTITIONED)',count(*) as cnt from cdb_indexes where partitioned='NO' group by owner,con_id
    union
    select owner,con_id,'INDEX (PARTITIONED)',count(*) as cnt from cdb_indexes where partitioned='YES' group by owner,con_id
    union
    select owner,con_id,'LOBS (NON PARTITIONED)',count(*) as cnt from cdb_lobs where partitioned='NO' group by owner,con_id
    union
    select owner,con_id,'LOBS (PARTITIONED)',count(*) as cnt from cdb_lobs where partitioned='YES' group by owner,con_id
) u,
(select distinct con_id as con_id, name from gv$containers) cont
WHERE u.CON_ID=cont.con_id
AND OWNER in (select username from cdb_users where oracle_maintained='N' and cont.con_id=u.con_id)
ORDER BY 1,2

