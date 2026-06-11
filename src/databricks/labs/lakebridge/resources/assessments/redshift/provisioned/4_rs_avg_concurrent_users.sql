-- 4
with base as (select count(distinct userid) as distinct_users, date_part(hour,starttime) as hour
                from query_view 
                group by 2
                order by 2)
select 'rs_avg_concurrent_users' set_name
      ,round(avg(distinct_users),0) avg_concurrent_users from base
;
