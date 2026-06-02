--10. Cost incurred
select 'cost_incurred' set_name
      ,trunc(start_time) "day"
      ,(sum(charged_seconds)::double precision) seconds_charged
      ,(sum(charged_seconds)/3600::double precision) * 0.36 as cost_incurred 
 from sys_serverless_usage 
group by 1,2 
order by 1,2
;
