-- 9 - Chart: cpu_consumption_by_hour_and_query_type
with query_metrics as (select user_id
                               ,query_id 
                               ,duration/1000 as run_time_ms
                           from sys_query_detail
                          where step_id = -1)
      ,query_overview as (select * from query_view natural join query_metrics)
select 'chart_cpu_consumption_by_hour_and_query_type' set_name
      ,query_type
      ,sum(run_time_ms) as sum_cpu_time
      ,date_part(hour,start_time) as hour
  from query_overview
 where run_time_ms > 0
 group by 1,2,4
 order by 4 asc, sum_cpu_time asc
;
