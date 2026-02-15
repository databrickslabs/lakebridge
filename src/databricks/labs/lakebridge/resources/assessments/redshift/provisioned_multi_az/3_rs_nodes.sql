-- 3 - NOT AVAILABLE FOR MULTI_AZ (CLI, boto3, CloudWatch or UI); placeholder returns empty row
select 'rs_nodes' as set_name, cast(null as varchar(64)) as rs_nodes_type, cast(null as int) as rs_number_of_nodes where 1=0;
