/* --width 3 --height 6 --order 8 --title 'Avg Query Volume (10 Minute Interval)' --type table */
with dedicated_session_requests as (
  select
    pool_name,
    session_id,
    request_id,
    command,
    start_time,
    end_time,
    command_w1,
    command_w2,
    command_type
  from
    IDENTIFIER('`synapse-profiler-runs`.`run-name`.`dedicated_session_requests`')
  qualify
    row_number() OVER (partition by pool_name, session_id, request_id order by end_time) = 1
),
commands as (
  select
    pool_name,
    session_id,
    request_id,
    command,
    from_utc_timestamp(start_time, 'US/Eastern') start_time,
    from_utc_timestamp(end_time, 'US/Eastern') end_time,
    command_w1,
    command_w2,
    command_type,
    CASE
      WHEN command_type in ('DML', 'ROUTINE', 'DDL') THEN 'ETL'
      WHEN command_type = 'QUERY' THEN 'SQL Serving'
      ELSE 'OTHER'
    END as workload_type
  from
    dedicated_session_requests
  where
    start_time is not null
    and end_time is not null
    AND command_w1 not in ('SET', 'USE')
    AND not (
      command_w1 = 'SELECT'
      and command_w2 = '@@SPID;'
    )
    AND not (
      command_w1 = 'EXEC'
      and command_w2 = '[SP_EXECUTESQL]'
    )
),
timex_10min_windows as (
  select
    make_timestamp(year(dt), month(dt), day(dt), t_hours.hr, t_minutes.min * 10, 00) as st,
    make_timestamp(
      year(dt), month(dt), day(dt), t_hours.hr, (t_minutes.min + 1) * 10 - 1, 59.999
    ) as ed
  from
    (
      select distinct
        date(start_time) dt
      from
        commands
      union
      select distinct
        date(end_time) dt
      from
        commands
    ) x,
    (
      select
        explode(sequence(0, 23)) hr
    ) t_hours,
    (
      select
        explode(sequence(0, 5)) min
    ) t_minutes
),
daily_10min_interval_metrics as (
  select
    date_format(st, "HH:mm") as st,
    workload_type,
    command_type,
    avg(query_count) avg_query_count,
    min(query_count) min_query_count,
    max(query_count) max_query_count
  from
    (
      select
        st,
        ed,
        workload_type,
        command_type,
        count(distinct request_id) as query_count
      from
        timex_10min_windows X
          left join commands Y
            on (start_time between X.st and X.ed)
            or (end_time between X.st and X.ed)
      group by
        1,
        2,
        3,
        4
    )
  group by
    1,
    2,
    3
  order by
    1,
    2,
    3
)
select
  workload_type,
  sum(avg_query_count) over (partition by st, workload_type) / 10 as queries_per_minute_by_workload
from
  daily_10min_interval_metrics;
