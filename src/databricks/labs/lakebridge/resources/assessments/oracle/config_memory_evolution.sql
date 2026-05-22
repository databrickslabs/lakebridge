select NVL(con.name,'Entire CDB/Non CDB') con_name,
	    param.instance_number,
	    snap.snap_time,
	    parameter_name,
	    value
from cdb_hist_parameter param,
(select con_id,name from v$containers) con,
(select con_id,snap_id,instance_number,begin_interval_time snap_time from cdb_hist_snapshot) snap
where 1=1
and param.snap_id=snap.snap_id
and param.con_id=con.con_id(+)
and param.instance_number=snap.instance_number
and param.parameter_name in ('sga_target',
                             'pga_aggregate_target',
                             'db_cache_size',
                             'shared_pool_size',
                             'large_pool_size',
                             'java_pool_size',
                             'streams_pool_size',
                             'db_16k_cache_size',
                             'db_2k_cache_size',
                             'db_32k_cache_size',
                             'db_4k_cache_size',
                             'db_8k_cache_size',
                             'memory_target',
                             'memory_max_target')
order by 1,3,2
