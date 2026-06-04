-- 8 - Chart: concurrent_users_by_hour
select 'chart_concurrent_users_by_hour' set_name
      ,count(distinct userid) as distinct_users
      ,date_part(hour,starttime) as hour
from query_view 
group by 1,3
order by 3
;
