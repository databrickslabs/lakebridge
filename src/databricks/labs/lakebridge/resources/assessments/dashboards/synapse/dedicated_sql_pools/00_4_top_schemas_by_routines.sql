/* --width 3 --height 6 --order 4 --title 'Top 10 Schemas by Routines' --type table */
WITH dedicated_routines as (
  select
    routine_catalog,
    routine_schema,
    routine_type,
    routine_name
  from
    IDENTIFIER('`synapse-profiler-runs`.`run-name`.`dedicated_routines`')
),
routines as (
  select
    routine_catalog,
    routine_schema,
    routine_type,
    routine_name
  from
    dedicated_routines
  qualify
    row_number() over (
        partition by routine_catalog, routine_schema, routine_name
        order by routine_name
      ) = 1
),
routine_counts as (
  select
    routine_catalog as catalog,
    routine_schema as schema,
    routine_type as object_type,
    count(*) as num_objects
  from
    routines
  group by
    1,
    2,
    3
  order by
    4 desc
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
      routine_counts
  ) X
order by
  4 desc;
