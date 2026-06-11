-- 2 - NOT AVAILABLE FOR MULTI_AZ (CLI or CloudWatch or UI); placeholder returns empty row
select 'rs_managed_storage_gb' as set_name, cast(null as decimal(18,2)) as rs_managed_storage_gb where 1=0;
