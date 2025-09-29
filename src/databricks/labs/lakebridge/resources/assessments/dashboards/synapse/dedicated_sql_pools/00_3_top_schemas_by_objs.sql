/* --width 3 --height 6 --order 3 --title 'Top 10 Schemas by Objects' --type table */
with dedicated_tables as (
  select
    table_catalog,
    table_schema,
    table_name,
    table_type
  from
    IDENTIFIER('`synapse-profiler-runs`.`run-name`.`dedicated_tables`')
),
dedicated_views as (
  select
    table_catalog,
    table_schema,
    table_name
  from
    IDENTIFIER('`synapse-profiler-runs`.`run-name`.`dedicated_views`')
),
tables as (
  select
    table_catalog,
    table_schema,
    table_name
  from
    dedicated_tables
  where
    table_type != 'VIEW'
  qualify
    row_number() over (partition by table_catalog, table_schema, table_name order by table_name) = 1
),
table_counts as (
  select
    table_catalog as catalog,
    table_schema as schema,
    'Tables' as object_type,
    count(*) as num_objects
  from
    tables
  group by
    1,
    2
  order by
    3 desc
  limit 10
),
views as (
  select
    table_catalog,
    table_schema,
    table_name
  from
    dedicated_views
  qualify
    row_number() over (partition by table_catalog, table_schema, table_name order by table_name) = 1
),
view_counts as (
  select
    table_catalog as catalog,
    table_schema as schema,
    'Views' as object_type,
    count(*) as num_objects
  from
    views
  group by
    1,
    2
  order by
    3 desc
  limit 10
)
select
  catalog,
  schema,
  object_type,
  num_objects
from
  (
    select
      catalog,
      schema,
      object_type,
      num_objects
    from
      table_counts
    union all
    select
      catalog,
      schema,
      object_type,
      num_objects
    from
      view_counts
  ) X
order by
  3 desc;
