-- 7 - Chart: cpu_consumption_by_query_type
-- One row per query: summing sys_query_detail segment rows double-counted segments that run in parallel.
with query_metrics as (select user_id
                               ,query_id 
                               ,execution_time/1000.0 as run_time_ms
                           from sys_query_history)
      ,query_overview as (select * from query_view natural join query_metrics)
select 'chart_cpu_consumption_by_query_type' set_name
      ,query_type
      ,sum(run_time_ms)::double precision as sum_cpu_time
  from query_overview
 where run_time_ms > 0
 group by 1,2
;
