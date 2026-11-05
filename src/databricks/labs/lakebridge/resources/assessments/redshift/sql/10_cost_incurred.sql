--10. Cost incurred
select 'cost_incurred' set_name
      ,trunc(start_time) "day"
      ,(sum(charged_seconds)::double precision) seconds_charged
 from sys_serverless_usage 
group by 1,2
;
