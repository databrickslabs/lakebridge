-- 6 - Chart: query_type_by_hour
select 'chart_query_type_by_hour' set_name
      ,query_type
      ,date_part(hour,starttime) as hour
      ,count(*) as count
  from query_view
 group by 1, 2, 3
 order by hour
;
