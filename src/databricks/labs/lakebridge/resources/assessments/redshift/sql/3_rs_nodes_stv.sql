-- 3
with par as (select capacity
                   ,mount like '/dev/nvme%' as is_nvme
                   ,count(1) as part_count
               from stv_partitions
              where host = 0 and owner = 0
              group by 1, 2
              order by 1 desc
              limit 1)
select 'rs_nodes' set_name 
      ,case
         when	capacity = 190633	and	not	is_nvme then 'dc1.large'
         when	capacity = 380319					then 'dc1.8xlarge'
         when	capacity = 190633	and	is_nvme		then 'dc2.large'
         when	capacity = 760956					then 'dc2.8xlarge'
         when	capacity = 726296					then 'dc2.8xlarge'
         when	capacity = 952455					then 'ds2.xlarge'
         when	capacity = 945026					then 'ds2.8xlarge'
         when capacity = 869530         then 'ra3.large'
         when	capacity = 2002943 and part_count=1 then 'ra3.xlplus'
         when	capacity = 6772561 and part_count=1 then 'ra3.4xlarge'
         when	capacity = 6772561 and part_count=4 then 'ra3.16xlarge'
         else	'unknown'
       end as	rs_nodes_type
      ,(select count(distinct	host)	from stv_partitions) as	rs_number_of_nodes
  from par
;
