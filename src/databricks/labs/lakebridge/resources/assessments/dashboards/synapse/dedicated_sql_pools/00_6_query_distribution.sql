/* --width 3 --height 6 --order 6 --title 'Query Distribution' --type table */
with dedicated_session_requests as (
  select
    *
  from
    IDENTIFIER('`synapse-profiler-runs`.`run-name`.`dedicated_session_requests`')
),
commands as (
  select
    pool_name,
    session_id,
    request_id,
    command,
    start_time,
    end_time,
    (
      unix_millis(from_utc_timestamp(end_time, 'US/Eastern'))
      - unix_millis(from_utc_timestamp(start_time, 'US/Eastern'))
    ) as exec_wall_misecs,
    command_w1,
    command_w2,
    command_type
  from
    dedicated_session_requests
  qualify
    row_number() OVER (partition by pool_name, session_id, request_id order by end_time) = 1
),
data_commands as (
  select
    pool_name,
    session_id,
    request_id,
    command,
    start_time,
    end_time,
    exec_wall_misecs,
    command_w1,
    command_w2,
    command_type
  from
    commands
  where
    command_w1 not in ('SET', 'USE')
    AND not (
      command_w1 = 'SELECT'
      and command_w2 = '@@SPID;'
    )
    AND not (
      command_w1 = 'EXEC'
      and command_w2 = '[SP_EXECUTESQL]'
    )
),
typed_commands as (
  select
    pool_name,
    session_id,
    request_id,
    command,
    start_time,
    end_time,
    exec_wall_misecs,
    command_w1,
    command_w2,
    command_type
  from
    data_commands
),
typed_commands_by_volume as (
  select
    command_type,
    'Volume' as metric_type,
    count(*) as value
  from
    typed_commands
  group by
    1,
    2
  order by
    3 desc
),
typed_commands_by_time as (
  select
    command_type,
    'Time (Wall)' as metric_type,
    sum(exec_wall_misecs) / 1000 as value
  from
    typed_commands
  group by
    1,
    2
  order by
    3 desc
)
select
  command_type,
  metric_type,
  sum(value)
from
  typed_commands_by_volume
group by command_type, metric_type
union all
select
  command_type,
  metric_type,
  sum(value)
from
  typed_commands_by_time
group by command_type, metric_type;
