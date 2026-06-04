-- 9 - Chart: cpu_consumption_by_hour_and_query_type
with query_metrics as (
    select 
        username as userid,
        query_type,
        start_time,
        case when execution_time = -1 then 0 else execution_time / 1000000.0 end as cpu_time,
        case when elapsed_time = -1 then 0 else elapsed_time / 1000000.0 end as run_time
    from sys_query_history
    where status in ('success', 'running')
)
select 
    'chart_cpu_consumption_by_hour_and_query_type' as set_name,
    query_type,
    sum(cpu_time) as sum_cpu_time,
    date_part(hour, start_time) as hour
from query_metrics
where cpu_time > 0
group by query_type, date_part(hour, start_time)
order by hour asc, sum_cpu_time asc
;
