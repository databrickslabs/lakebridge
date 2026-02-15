-- 9 - Chart: cpu_consumption_by_hour_and_query_type
with query_metrics as (select userid
                             ,query
                             ,case when cpu_time = -1 then 0 else cpu_time/1000000 end as cpu_time
                             ,case when run_time = -1 then 0 else run_time/1000000 end as run_time
                         from stl_query_metrics
                        where segment = -1 and step_type = -1)
    ,query_overview as (select * from query_view natural join query_metrics)
select 'chart_cpu_consumption_by_hour_and_query_type' set_name
      ,query_type
      ,sum(cpu_time) as sum_cpu_time
      ,date_part(hour,starttime) as hour
  from query_overview
 where cpu_time > 0
 group by 1,2,4
 order by 4 asc, sum_cpu_time asc
;
