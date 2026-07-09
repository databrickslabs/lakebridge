--3
with cte as (select compute_capacity as rs_nodes_type
                   ,sum(compute_seconds) as compute_seconds
               from sys_serverless_usage 
              where compute_capacity > 0
              group by 1)
select 'rs_nodes' as set_name
      ,concat(rs_nodes_type, ' RPUs') as rs_nodes_type
      ,0 as rs_number_of_nodes,
    compute_seconds
from cte;
