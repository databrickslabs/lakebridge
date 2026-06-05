from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path

import duckdb
from faker import Faker

ORACLE_RESOURCE_PACKAGE = "databricks.labs.lakebridge.resources.assessments.oracle"


@dataclass(frozen=True)
class MockTableDefinition:
    schema: str
    generator: Callable
    num_rows: int


class SynapseProfilerBuilder:
    """
    Simulates the extraction of usage/metrics data from the Azure Synapse profiler.
    """

    def __init__(self, tables_dict: dict, db_path=":memory:"):
        self.conn = duckdb.connect(database=db_path)
        self.fake = Faker()
        self.tables_dict = tables_dict
        self._create_all_tables()

    def _create_all_tables(self) -> None:
        for table_name, table_def in self.tables_dict.items():
            schema = table_def.schema
            sql_stmnt = f"CREATE OR REPLACE TABLE {table_name} ({schema});"
            try:
                self.conn.execute(sql_stmnt)
            except Exception as e:
                print(f"Error creating table {table_name}: {e}")
                print(sql_stmnt)
                raise e

    def create_sample_data(self) -> None:
        for table_name, table_def in self.tables_dict.items():
            generator = table_def.generator
            row_count = table_def.num_rows
            for _ in range(row_count):
                values = generator(self.fake)
                placeholders = ", ".join(["?"] * len(values))
                self.conn.execute(f"INSERT INTO {table_name} VALUES ({placeholders})", values)

    def display_tables(self) -> None:
        for table_name in self.tables_dict.keys():
            print(f"\n--- {table_name.upper()} ---")
            print(self.conn.execute(f"SELECT * FROM {table_name}").df())

    def shutdown(self) -> None:
        self.conn.close()


def generate_dedicated_sql_pool_metrics(fake) -> tuple[int, int, int, int, int, int, int, int]:
    metric_values = [fake.random_int(10, 1000) for _ in range(10)]
    count = len(metric_values)
    total = sum(metric_values)
    average = total / count
    minimum = min(metric_values)
    maximum = max(metric_values)

    name = fake.random_element(
        [
            "cpu_percent",
            "memory_percent",
            "requests_queued",
            "temp_db_usage",
            "log_write_throughput",
            "cache_hit_percent",
            "io_read_ops",
            "io_write_ops",
        ]
    )

    # More realistic pool names
    pool_name = fake.random_element(
        [
            f"DW{fake.random_element([100, 200, 300, 1000, 3000])}c",
            f"sqlpool{fake.random_int(1, 25):02}",
            f"{fake.random_element(['finance', 'analytics', 'sales', 'prod'])}_pool",
            f"{fake.random_element(['etl', 'reporting', 'ad_hoc'])}_dw",
        ]
    )

    return (
        round(average, 2),
        count,
        maximum,
        minimum,
        name,
        fake.date_time_between("-1d", "now").isoformat(),
        round(total, 2),
        pool_name,
    )


def generate_sql_pools(fake) -> tuple[datetime, str, str, str, str, dict[str, str], str, str]:
    """
    1.2
    Workspace SQL Pools
    """
    return (
        fake.date_time_between(start_date="-2y", end_date="now").isoformat(),  # creation_date
        fake.uuid4(),  # id
        fake.random_element(["eastus", "westeurope", "centralus"]),  # location
        f"sqlpool_{fake.word()}_{fake.random_int(1, 999)}",  # name
        fake.random_element(["Succeeded", "Updating", "Deleting", "Failed"]),  # provisioning_state
        {
            "capacity": fake.random_element([100, 200, 300, 1000]),
            "name": fake.random_element(["DW100c", "DW200c", "DW1000c"]),
        },  # sku
        fake.random_element(["Online", "Paused", "Resuming", "Scaling"]),  # status
        "Microsoft.Synapse/workspaces/sqlPools",  # type
    )


def generate_dedicated_storage_info(fake) -> tuple[int, int, datetime, int]:
    reserved = fake.random_int(min=100_000, max=1_000_000)  # MB
    used = fake.random_int(min=0, max=reserved)  # Must be <= reserved
    return (
        reserved,  # ReservedSpaceMB
        used,  # UsedSpaceMB
        fake.date_time_between("-7d", "now").isoformat(),  # extract_ts
        fake.random_int(min=1, max=1000),  # node_id
    )


table_definitions = {
    "dedicated_sql_pool_metrics": MockTableDefinition(
        schema="""
            average DOUBLE,
            count BIGINT,
            maximum BIGINT,
            minimum BIGINT,
            name VARCHAR,
            timestamp VARCHAR,
            total DOUBLE,
            pool_name VARCHAR
        """,
        generator=generate_dedicated_sql_pool_metrics,
        num_rows=1000,
    ),
    "dedicated_storage_info": MockTableDefinition(
        schema="""
            ReservedSpaceMB BIGINT,
            UsedSpaceMB BIGINT,
            extract_ts VARCHAR,
            node_id BIGINT
        """,
        generator=generate_dedicated_storage_info,
        num_rows=0,  # Create an empty table for testing
    ),
    "workspace_sql_pools": MockTableDefinition(
        schema="""
            creation_date VARCHAR,
            id VARCHAR,
            location VARCHAR,
            name VARCHAR,
            provisioning_state VARCHAR,
            sku STRUCT(
                capacity BIGINT,
                name VARCHAR
            ),
            status VARCHAR,
            type VARCHAR
        """,
        generator=generate_sql_pools,
        num_rows=1000,
    ),
}


def build_mock_synapse_extract(extract_db_name: str, path_prefix: Path) -> Path:
    synapse_extract_path = path_prefix
    synapse_extract_path.mkdir(parents=True, exist_ok=False)
    full_synapse_extract_path = synapse_extract_path / f"{extract_db_name}.db"
    builder = SynapseProfilerBuilder(table_definitions, db_path=str(full_synapse_extract_path))
    builder.create_sample_data()
    builder.shutdown()
    return full_synapse_extract_path


# --- Redshift mock (same pattern as Synapse, no live DB) ---


def _generate_query_view(fake) -> tuple[int, int, str, str, str, str, str]:
    return (
        fake.random_int(1, 10000),
        fake.random_int(1, 100000),
        fake.sentence(),
        fake.date_time_between("-7d", "now").isoformat(),
        fake.date_time_between("-7d", "now").isoformat(),
        fake.word(),
        fake.random_element(["SELECT", "INSERT", "UPDATE", "DELETE"]),
    )


def _generate_rs_managed_storage_gb(fake) -> tuple[str, float]:
    return (fake.word(), round(fake.random.uniform(1.0, 1000.0), 2))


def _generate_rs_nodes(fake) -> tuple[str, str, int, int]:
    return (
        fake.word(),
        fake.random_element(["ra3.xlplus", "ra3.4xlarge"]),
        fake.random_int(1, 10),
        fake.random_int(0, 100000),
    )


redshift_table_definitions = {
    "query_view": MockTableDefinition(
        schema="""
            user_id BIGINT,
            query_id BIGINT,
            query_text VARCHAR,
            start_time VARCHAR,
            end_time VARCHAR,
            query_group VARCHAR,
            query_type VARCHAR
        """,
        generator=_generate_query_view,
        num_rows=1000,
    ),
    "rs_managed_storage_gb": MockTableDefinition(
        schema="""
            set_name VARCHAR,
            rs_managed_storage_gb DOUBLE
        """,
        generator=_generate_rs_managed_storage_gb,
        num_rows=1000,
    ),
    "rs_nodes": MockTableDefinition(
        schema="""
            set_name VARCHAR,
            rs_nodes_type VARCHAR,
            rs_number_of_nodes BIGINT,
            compute_seconds BIGINT
        """,
        generator=_generate_rs_nodes,
        num_rows=0,
    ),
}


class RedshiftProfilerBuilder(SynapseProfilerBuilder):
    """Simulates Redshift profiler extract (DuckDB, no live Redshift)."""

    def __init__(self, tables_dict: dict, db_path: str = ":memory:"):
        super().__init__(tables_dict, db_path)


def build_mock_redshift_extract(extract_db_name: str, path_prefix: Path) -> Path:
    path_prefix.mkdir(parents=True, exist_ok=False)
    full_path = path_prefix / f"{extract_db_name}.db"
    builder = RedshiftProfilerBuilder(redshift_table_definitions, db_path=str(full_path))
    builder.create_sample_data()
    builder.shutdown()
    return full_path


def _oracle_sample_value(duckdb_type: str):
    """Return a type-appropriate sample value for a DuckDB column type."""
    duckdb_type = duckdb_type.upper()
    if duckdb_type == "TIMESTAMP":
        return datetime(2026, 4, 3, 13, 55, 26)
    if duckdb_type in {"INTEGER", "BIGINT"}:
        return 1
    if duckdb_type == "DOUBLE":
        return 1.5
    return "sample"  # VARCHAR / fallback


def build_mock_oracle_extract(extract_db_name: str, path_prefix: Path) -> Path:
    """
    Build a mock Oracle profiler extract whose table schemas come directly from the shipped Oracle
    DDL resources. Because the tables are created from the same DDL the real profiler uses, validating
    this extract against the shipped ``oracle_extract_schema.yml`` exercises DDL <-> schema-definition
    consistency without needing a live Oracle database.

    Each table is seeded with rows so empty-table checks pass. ``config_db_features.inst_id`` gets a row
    with a NULL value to mirror the real extract's nullable-integer edge case.
    """
    oracle_extract_path = path_prefix
    oracle_extract_path.mkdir(parents=True, exist_ok=False)
    full_oracle_extract_path = oracle_extract_path / f"{extract_db_name}.db"

    ddl_files = sorted(
        (f for f in resources.files(ORACLE_RESOURCE_PACKAGE).iterdir() if f.name.endswith("_ddl.sql")),
        key=lambda f: f.name,
    )
    with duckdb.connect(database=str(full_oracle_extract_path)) as conn:
        for ddl_file in ddl_files:
            conn.execute(ddl_file.read_text(encoding="utf-8"))

        for (table_name,) in conn.execute("SHOW TABLES").fetchall():
            columns = conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                f"WHERE table_name = '{table_name}' ORDER BY ordinal_position"
            ).fetchall()
            row = [_oracle_sample_value(data_type) for _, data_type in columns]
            # Mirror the real extract: config_db_features.inst_id (INTEGER) contains NULLs.
            null_row = [None if name == "inst_id" else value for (name, _), value in zip(columns, row)]
            placeholders = ", ".join(["?"] * len(row))
            conn.execute(f"INSERT INTO {table_name} VALUES ({placeholders})", row)
            conn.execute(f"INSERT INTO {table_name} VALUES ({placeholders})", null_row)
    return full_oracle_extract_path
