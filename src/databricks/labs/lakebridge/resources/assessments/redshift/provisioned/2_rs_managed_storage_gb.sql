-- 2
select 'rs_managed_storage_gb' set_name
      ,round((sum(used) / 1024), 2) as  rs_managed_storage_gb
  from stv_node_storage_capacity
;
