-- Serverless usage from sys_serverless_usage: one row per (day, RPU capacity level).
-- Combines compute (compute_seconds) with billing (charged_seconds, rpu_hours).
-- Granularity preserved: sum over capacity -> daily totals; sum over days -> per-capacity totals.
select trunc(start_time) as "day"
      ,compute_capacity as rpu_capacity
      ,sum(compute_seconds)::double precision as compute_seconds
      ,sum(charged_seconds)::double precision as charged_seconds
      ,(sum(charged_seconds) / 3600.0)::double precision as rpu_hours
  from sys_serverless_usage
 where charged_seconds > 0 or compute_seconds > 0  -- any billed/used period; a billed row can report 0 capacity
 group by trunc(start_time), compute_capacity
;
