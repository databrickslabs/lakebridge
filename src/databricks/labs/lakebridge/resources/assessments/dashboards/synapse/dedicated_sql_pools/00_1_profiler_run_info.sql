/* --width 20 --height 6 --order 1 --title 'Profiler Extract Info' --type table */
WITH dedicated_session_requests as (
  select
    *
  from
    IDENTIFIER('`synapse-profiler-runs`.`run-name`.`dedicated_session_requests`')
  qualify
    row_number() over (PARTITION BY pool_name, session_id, request_id order by end_time desc) = 1
)
select
  extract_ts,
  count(*) AS requests
FROM
  dedicated_session_requests
GROUP BY
  1
ORDER BY
  1;
