-- query view (provisioned_multi_az: sys_query_history)
create or replace view query_view 
as
(select  user_id,
       query_id,
       query_text,
       cast(start_time as timestamp) as start_time,
       cast(end_time as timestamp) as end_time
      ,case
         when query_label like 'stmt%' or query_label like 'statement%' then 'other'
         else query_label
       end as query_group
      ,case
         when lower(query_text) like '%create % table % as %' then 'Transform'
         when lower(query_text) like '%create % temp % table %' then 'Transform'
         when lower(query_text) like '%load %' then 'Extract and Load'
         when lower(query_text) like '%unload %' then 'Extract and Load'
         when lower(query_text) like 'copy % to %' then 'Extract and Load'
         when lower(query_text) like 'copy % from %' then 'Ingestion'
         when lower(query_text) like '%insert %' 
                                or lower(query_text) like '%update %' 
                                or lower(query_text) like '%delete %' then 'Transform'
         when lower(query_text) like '%stv_recents%' 
                                or lower(query_text) like '%padb_fetch_sample%' 
                                or lower(query_text) like '%copy%analyze%' 
                                or lower(query_text) like '%analyze%compression%' then 'System'
         when lower(query_text) like '%select %' 
                                or lower(query_text) like '%with %' 
                                or lower(query_text) like 'fetch %' then 'BI Query'
         else 'Unknown'
       end as query_type
  from sys_query_history 
 where query_label not in('metrics', 'other', 'health', 'cmstats') and (query_label not like 'statement%' and query_label not like 'stmt%')
 )
;
