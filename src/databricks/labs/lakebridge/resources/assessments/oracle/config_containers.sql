select listagg(inst_id, ',') within group (order by name) as inst_ids,name,open_mode,pdb_count from gv$containers group by name,open_mode,pdb_count
