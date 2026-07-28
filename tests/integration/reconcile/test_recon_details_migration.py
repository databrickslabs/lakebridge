import importlib.util
from pathlib import Path

import pytest

import databricks.labs.lakebridge


@pytest.fixture(scope="module")
def migration():
    """The v0.15.0 upgrade script, loaded by path because its module name is not importable."""
    path = Path(databricks.labs.lakebridge.__file__).parent / "upgrades" / "v0.15.0_migrate_recon_details_to_variant.py"
    spec = importlib.util.spec_from_file_location("v0_15_0_migrate_recon_details_to_variant", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migrate_details_to_record_level_schema(ws, spark, recon_schema, migration):
    prefix = f"{recon_schema.catalog_name}.{recon_schema.name}"
    spark.sql(
        f"CREATE TABLE {prefix}.details (recon_table_id BIGINT NOT NULL, recon_type STRING NOT NULL, "
        "status BOOLEAN NOT NULL, data ARRAY<MAP<STRING, STRING>> NOT NULL, inserted_ts TIMESTAMP NOT NULL)"
    )
    spark.sql(f"""INSERT INTO {prefix}.details VALUES
        (1, 'mismatch', false,
         array(map('s_suppkey', '11',
                   's_name_base', 'source name', 's_name_compare', 'target name', 's_name_match', 'false',
                   's_address_base', 'same address', 's_address_compare', 'same address', 's_address_match', 'true')),
         timestamp'2025-01-01 00:00:00'),
        (1, 'missing_in_source', false, array(map('s_suppkey', '22', 's_name', 'target only')),
         timestamp'2025-01-01 00:00:00'),
        (1, 'missing_in_target', false,
         array(map('s_suppkey', '33', 's_name', 'source only'), map('s_suppkey', '44', 's_name', 'source only too')),
         timestamp'2025-01-01 00:00:00'),
        (1, 'schema', true,
         array(map('source_column', 's_name', 'source_datatype', 'varchar', 'databricks_column', 's_name',
                   'databricks_datatype', 'string', 'is_valid', 'true')),
         timestamp'2025-01-01 00:00:00')""")

    assert migration.migrate_details_table(ws, prefix)

    # Legacy rows are exploded to one row per record; schema rows are split out into schema_details.
    by_type = {
        row.recon_type: row.rows
        for row in spark.sql(f"SELECT recon_type, count(*) AS rows FROM {prefix}.details GROUP BY recon_type").collect()
    }
    assert by_type == {"mismatch": 1, "missing_in_source": 1, "missing_in_target": 2}

    mismatch = spark.sql(
        f"SELECT to_json(record_key) AS record_key, source_row:s_name::string AS source_name, "
        f"target_row:s_name::string AS target_name, source_row:s_address::string AS source_address, "
        f"mismatch_columns FROM {prefix}.details WHERE recon_type = 'mismatch'"
    ).collect()[0]
    assert mismatch.record_key == '{"s_suppkey":"11"}'
    assert mismatch.source_name == "source name"
    assert mismatch.target_name == "target name"
    assert mismatch.source_address == "same address"
    assert mismatch.mismatch_columns == ["s_name"]

    missing_in_source = spark.sql(
        f"SELECT source_row, target_row:s_name::string AS target_name FROM {prefix}.details "
        f"WHERE recon_type = 'missing_in_source'"
    ).collect()[0]
    assert missing_in_source.source_row is None
    assert missing_in_source.target_name == "target only"

    missing_in_target = spark.sql(
        f"SELECT source_row:s_name::string AS source_name, target_row FROM {prefix}.details "
        f"WHERE recon_type = 'missing_in_target' AND record_key:s_suppkey::string = '33'"
    ).collect()[0]
    assert missing_in_target.source_name == "source only"
    assert missing_in_target.target_row is None

    schema_rows = spark.sql(f"SELECT * FROM {prefix}.schema_details").collect()
    assert len(schema_rows) == 1
    assert schema_rows[0].source_column == "s_name"
    assert schema_rows[0].source_datatype == "varchar"
    assert schema_rows[0].databricks_column == "s_name"
    assert schema_rows[0].databricks_datatype == "string"
    assert schema_rows[0].is_valid is True

    # The raw legacy rows stay behind as a rollback path.
    assert spark.sql(f"SELECT count(*) AS rows FROM {prefix}.details_backup").collect()[0].rows == 4
    # A second run sees the new schema and does nothing.
    assert not migration.migrate_details_table(ws, prefix)


def test_migrate_aggregate_details_to_record_level_schema(ws, spark, recon_schema, migration):
    prefix = f"{recon_schema.catalog_name}.{recon_schema.name}"
    spark.sql(
        f"CREATE TABLE {prefix}.aggregate_details (recon_table_id BIGINT NOT NULL, rule_id BIGINT NOT NULL, "
        "recon_type STRING NOT NULL, data ARRAY<MAP<STRING, STRING>> NOT NULL, inserted_ts TIMESTAMP NOT NULL)"
    )
    spark.sql(f"""INSERT INTO {prefix}.aggregate_details VALUES
        (2, 7, 'mismatch',
         array(map('s_nationkey', '5', 'source_sum_s_acctbal', '100', 'target_sum_s_acctbal', '90')),
         timestamp'2025-01-01 00:00:00')""")

    assert migration.migrate_aggregate_details_table(ws, prefix)

    row = spark.sql(
        f"SELECT rule_id, recon_type, record_key:s_nationkey::string AS nation_key, "
        f"source_row:source_sum_s_acctbal::string AS source_sum, target_row, mismatch_columns "
        f"FROM {prefix}.aggregate_details"
    ).collect()[0]
    assert row.rule_id == 7
    assert row.recon_type == "mismatch"
    assert row.nation_key == "5"
    assert row.source_sum == "100"
    assert row.target_row is None
    assert row.mismatch_columns is None

    # The raw legacy rows stay behind as a rollback path.
    assert spark.sql(f"SELECT count(*) AS rows FROM {prefix}.aggregate_details_backup").collect()[0].rows == 1
    # A second run sees the new schema and does nothing.
    assert not migration.migrate_aggregate_details_table(ws, prefix)
