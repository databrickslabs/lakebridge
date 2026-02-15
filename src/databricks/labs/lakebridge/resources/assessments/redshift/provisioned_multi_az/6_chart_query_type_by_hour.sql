-- 6 - Chart: query_type_by_hour
select 
    'chart_query_type_by_hour' as set_name,
    query_type,
    date_part(hour, start_time) as hour,
    count(*) as count
from sys_query_history
group by query_type, date_part(hour, start_time)
order by hour;
