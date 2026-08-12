-- 8 - Chart: concurrent_users_by_hour
with daily_hourly_users as (select start_time::date as day
                                  ,date_part(hour, start_time) as hour
                                  ,count(distinct user_id) as distinct_users
                              from query_view
                             group by 1, 2)
select 'chart_concurrent_users_by_hour' set_name
      ,max(distinct_users) as distinct_users
      ,hour
  from daily_hourly_users
 group by 1, 3
 order by 3
;
