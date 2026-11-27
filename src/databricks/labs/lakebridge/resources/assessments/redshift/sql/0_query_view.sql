-- query view
create or replace view query_view 
as
(select user_id
       ,query_id
       ,query_text
       ,cast(start_time as timestamp)
       ,cast(end_time as timestamp)
      ,case
         when query_label like 'stmt%' or query_label like 'statement%' then 'other'
         else query_label
       end as query_group
      ,case
         -- '%create %table %' rather than '%create % table %': plain `create table t as` has one space between the keywords
         when qt like '%create %table % as %' then 'Transform'
         when qt like '%create % temp % table %' then 'Transform'
         when qt like '%load %' then 'Extract and Load'
         when qt like '%unload %' then 'Extract and Load'
         when qt like 'copy % to %' then 'Extract and Load'
         when qt like 'copy % from %' then 'Ingestion'
         when qt like '%insert %'
                                or qt like '%update %'
                                or qt like '%delete %' then 'Transform'
         -- statements with no INSERT/UPDATE/DELETE keyword that still write data
         when ltrim(qt) like 'select % into %'
                                or ltrim(qt) like 'merge %'
                                or ltrim(qt) like 'refresh materialized view %'
                                or ltrim(qt) like 'call %' then 'Transform'
         when qt like '%stv_recents%'
                                or qt like '%padb_fetch_sample%'
                                or qt like '%copy%analyze%'
                                or qt like '%analyze%compression%' then 'System'
         when qt like '%select %'
                                or qt like '%with %'
                                or qt like 'fetch %' then 'BI Query'
         else 'Unknown'
       end as query_type
  -- tabs and newlines become spaces so the single-space LIKE patterns match multi-line SQL
  from (select *, translate(lower(query_text), chr(9) || chr(10) || chr(13), '   ') as qt
          from sys_query_history) h
 where query_label not in('metrics', 'other', 'health', 'cmstats')
   and (query_label not like 'statement%' 
   and query_label not like 'stmt%')
 )
;
