with t as (select version from product_component_version where product like 'Oracle Database%'),
	  inst_cnt as (select count(*) as cnt from gv$instance),
	  pdb_cnt as (select count(distinct con_id ) as cnt from gv$pdbs where name!='PDB$SEED'),
	  pdb_namelist as (select listagg(name,',') within group (order by name) as pdbnames from (select distinct name from gv$pdbs where name !='PDB$SEED')),
	  cpu_cores_global as (select 'CLUSTER' as scope, null , stat_name,'CPU GLOBAL (Cluster): '||stat_name as detailed_stat_name,sum(to_number(value)) as value
			from gv$osstat
			where stat_name in ('NUM_CPUS','NUM_CPU_CORES','NUM_CPU_SOCKETS')
			group by stat_name),
	  cpu_cores_details as (select 'INSTANCE' as scope,inst_id, stat_name,'CPU per Instance Id: '||inst_id||' - '||stat_name as detailed_stat_name, to_number(value) as value
				from gv$osstat
				where stat_name in ('NUM_CPUS','NUM_CPU_CORES','NUM_CPU_SOCKETS')
				order by inst_id,stat_name)
select * from
(
select null as scope, null as inst_id, null as stat_name, 'VERSION' as name,to_char(t.version) as value from t
union
select null as scope, null as inst_id, null as stat_name, 'INSTANCE COUNT', to_char(inst_cnt.cnt) from inst_cnt
union
select null as scope, null as inst_id, null as stat_name, 'PDB COUNT',to_char(pdb_cnt.cnt) from pdb_cnt
union
select null as scope, null as inst_id, null as stat_name, 'PDB LIST', to_char(pdb_namelist.pdbnames) from pdb_namelist
union
select scope, null as inst_id, stat_name,detailed_stat_name, to_char(value) from cpu_cores_global
union
select scope, inst_id, stat_name, detailed_stat_name, to_char(value) from cpu_cores_details
)
order by name
