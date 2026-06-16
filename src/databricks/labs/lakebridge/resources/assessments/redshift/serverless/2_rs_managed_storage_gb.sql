-- 2
select 'rs_managed_storage_gb' set_name
       ,round(avg(data_storage) / 1024.0, 2) as  rs_managed_storage_gb  
   from sys_serverless_usage
;
