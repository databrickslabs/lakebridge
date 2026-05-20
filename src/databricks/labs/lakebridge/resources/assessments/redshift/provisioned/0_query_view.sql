-- query view
create or replace view query_view 
as
(select userid, query, querytxt, cast(starttime as timestamp), cast(endtime as timestamp)
      ,case
         when label like 'stmt%' or label like 'statement%' then 'other'
         else label
       end as query_group
      ,case
         when lower(querytxt) like '%create % table % as %' then 'Transform'
         when lower(querytxt) like '%create % temp % table %' then 'Transform'
         when lower(querytxt) like '%load %' then 'Extract and Load'
         when lower(querytxt) like '%unload %' then 'Extract and Load'
         when lower(querytxt) like 'copy % to %' then 'Extract and Load'
         when lower(querytxt) like 'copy % from %' then 'Ingestion'
         when lower(querytxt) like '%insert %' 
                                or lower(querytxt) like '%update %' 
                                or lower(querytxt) like '%delete %' then 'Transform'
         when lower(querytxt) like '%stv_recents%' 
                                or lower(querytxt) like '%padb_fetch_sample%' 
                                or lower(querytxt) like '%copy%analyze%' 
                                or lower(querytxt) like '%analyze%compression%' then 'System'
         when lower(querytxt) like '%select %' 
                                or lower(querytxt) like '%with %' 
                                or lower(querytxt) like 'fetch %' then 'BI Query'
         else 'Unknown'
       end as query_type
  from stl_query 
 where label not in('metrics', 'other', 'health', 'cmstats') and (label not like 'statement%' and label not like 'stmt%')
 )
;
