-- 5
with m as (select date_trunc ('minute', starttime) start_time_minute
                 ,count(query) as query_cnt
             from query_view
            group by start_time_minute)
select 'rs_avg_queries_minute' set_name
      ,avg(query_cnt) avg_queries_minute
  from m
;
