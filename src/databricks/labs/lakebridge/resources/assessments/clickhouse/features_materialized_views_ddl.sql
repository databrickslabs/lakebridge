CREATE TABLE IF NOT EXISTS features_materialized_views (
    database VARCHAR,
    name VARCHAR,
    engine VARCHAR,
    as_select VARCHAR,
    dependencies_database VARCHAR,
    dependencies_table VARCHAR,
    create_table_query VARCHAR
);
