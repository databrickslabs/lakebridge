-- 2
select 'serverless' cluster_type
       ,round(avg(data_storage) / 1024.0, 2)::double precision as  rs_managed_storage_gb  
   from sys_serverless_usage
;
