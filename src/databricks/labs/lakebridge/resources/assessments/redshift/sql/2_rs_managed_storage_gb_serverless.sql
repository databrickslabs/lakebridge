-- 2
select 'rs_managed_storage_gb' set_name
       ,round(max(data_storage) / 1024.0, 2)::double precision as  rs_managed_storage_gb  
   from sys_serverless_usage
  -- latest snapshot: an average over the window understates storage on a growing estate
  where end_time = (select max(end_time) from sys_serverless_usage)
;
