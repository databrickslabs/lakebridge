--4
-- Distinct users are counted per calendar day and hour so a multi-day query history is not
-- collapsed into 24 hour-of-day buckets; the average is then taken over those buckets.
with base as (select date_trunc('day', start_time) as day
                    ,date_part(hour, start_time) as hour
                    ,count(distinct user_id) as distinct_users
                from query_view
                group by 1, 2)
select 'rs_avg_concurrent_users' set_name
      ,round(avg(distinct_users),0)::double precision avg_concurrent_users
  from base
;
