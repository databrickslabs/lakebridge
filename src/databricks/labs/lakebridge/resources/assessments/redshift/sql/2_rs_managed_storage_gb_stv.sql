-- 2
select 'provisioned' cluster_type
      ,round((sum(used) / 1024), 2)::double precision as  rs_managed_storage_gb
  from stv_node_storage_capacity
;
