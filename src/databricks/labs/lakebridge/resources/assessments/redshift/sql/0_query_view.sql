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
         when qt like '%stv_recents%'
                                or qt like '%padb_fetch_sample%'
                                or qt like '%copy%analyze%'
                                or qt like '%analyze%compression%' then 'System'
         -- The statement type Redshift records, whatever comments or line breaks the text has. CTAS
         -- also covers SELECT INTO and a materialized view refresh.
         when h.query_type in ('CTAS', 'INSERT', 'UPDATE', 'DELETE') then 'Transform'
         when h.query_type = 'COPY' then 'Ingestion'
         when h.query_type = 'UNLOAD' then 'Extract and Load'
         when h.query_type = 'SELECT' then 'BI Query'
         -- Other types go by the text: MERGE is OTHER, CALL is UTILITY.
         when qt like '%merge into %'
                                or qt like '%call %'
                                or qt like '%refresh materialized view %' then 'Transform'
         when qt like '%create % table % as %' then 'Transform'
         when qt like '%create % temp % table %' then 'Transform'
         when qt like '%load %' then 'Extract and Load'
         when qt like '%unload %' then 'Extract and Load'
         when qt like 'copy % to %' then 'Extract and Load'
         when qt like 'copy % from %' then 'Ingestion'
         when qt like '%insert %'
                                or qt like '%update %'
                                or qt like '%delete %' then 'Transform'
         when qt like '%select %'
                                or qt like '%with %'
                                or qt like 'fetch %' then 'BI Query'
         else 'Unknown'
       end as query_type
  -- query_text keeps a line break or tab as a backslash and a letter
  from (select *
              ,replace(replace(replace(lower(query_text), chr(92) || 'n', ' '), chr(92) || 't', ' '), chr(92) || 'r', ' ') as qt
          from sys_query_history) h
 where query_label not in('metrics', 'other', 'health', 'cmstats')
   and (query_label not like 'statement%'
   and query_label not like 'stmt%')
 )
;
