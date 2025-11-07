import os
from datetime import datetime
from dataclasses import dataclass
from collections.abc import Callable

import duckdb
from faker import Faker


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


def build_mock_synapse_extract(extract_db_name: str) -> str:
    synapse_extract_path = "/tmp/data/synapse_assessment"
    os.makedirs(synapse_extract_path, exist_ok=True)
    full_synapse_extract_path = f"{synapse_extract_path}/{extract_db_name}.db"
    builder = SynapseProfilerBuilder(table_definitions, full_synapse_extract_path)
    builder.create_sample_data()
    builder.shutdown()
    return full_synapse_extract_path
