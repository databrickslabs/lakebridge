-- 8 - Chart: concurrent_users_by_hour
select 'chart_concurrent_users_by_hour' set_name
      ,count(distinct user_id) as distinct_users
      ,date_part(hour,start_time) as hour
from query_view 
group by 1,3
order by 3
;
