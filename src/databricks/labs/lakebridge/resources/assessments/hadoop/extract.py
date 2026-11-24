"""Shared runner for the Hadoop profiler steps.

A step script fetches REST bodies, loads them into a temporary DuckDB table of JSON documents, runs its own
``<step>.sql`` over them, and writes the result into the profiler extract as a table typed by ``<step>_ddl.sql``.
It prints one JSON status line last, which ``pipeline.py`` reads.

Column names and types come from the DDL, so the SQL only has to produce the columns in the DDL's order.
"""

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pandas as pd
from databricks.labs.blueprint.entrypoint import get_logger

from databricks.labs.lakebridge.resources.assessments.common.cli import arguments_loader
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import connect_to_profiler_db
from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import (
    HadoopConfig,
    HadoopConfigError,
    load_hadoop_config,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import (
    RestClient,
    find_active_resource_manager,
)

logger = get_logger(__file__)

Endpoint = Literal["yarn", "spark_history"]
Fetch = Callable[[RestClient, HadoopConfig], list[str]]

# Session-scoped (TEMP) so they never persist into the profiler extract. Each takes a JSON value.
HELPER_MACROS: tuple[str, ...] = (
    # The RM's JSON gives a list as an array when it has several items, but as an object when it has one, and as a
    # string ("\n") or null when it is empty. Anything other than an array or an object yields an empty list.
    """CREATE TEMP MACRO hadoop_records(doc, path) AS (
        CASE json_type(doc, path)
            WHEN 'ARRAY' THEN CAST(json_extract(doc, path) AS JSON[])
            WHEN 'OBJECT' THEN [json_extract(doc, path)]
            ELSE CAST([] AS JSON[])
        END)""",
    # A scheduler queue's children: capacity schedulers nest them under queues.queue, fair schedulers under childQueues.
    """CREATE TEMP MACRO child_queues(node) AS (
        CASE WHEN json_type(node, '$.queues.queue') IS NOT NULL
            THEN hadoop_records(node, '$.queues.queue')
            ELSE hadoop_records(node, '$.childQueues')
        END)""",
)


def run_step(
    *,
    step: str,
    endpoint: Endpoint,
    fetch: Fetch,
    sql_dir: Path,
    db_path: str,
    creds_file: Path,
) -> str:
    """Extract one step and return its status message. Raises on failure."""
    config = load_hadoop_config(creds_file)
    if endpoint == "yarn":
        if config.yarn is None:
            raise HadoopConfigError("hadoop.yarn is not configured; the YARN steps need it")
        client = find_active_resource_manager(
            config.yarn.urls,
            username=config.yarn.username,
            password=config.yarn.password,
            verify=config.yarn.verify,
        )
    else:
        if config.spark_history is None:
            return "skipped: not configured (hadoop.spark_history is absent)"
        spark = config.spark_history
        client = RestClient(spark.urls[0], username=spark.username, password=spark.password, verify=spark.verify)

    # Masking runs on the raw responses before parsing, like the original profiler's SEARCH_REPLACE pipe.
    bodies = [config.mask(body) for body in fetch(client, config)]
    row_count = _write_table(
        step=step,
        sql_dir=sql_dir,
        db_path=db_path,
        bodies=bodies,
        params={"distribution": config.distribution},
    )
    return f"{step} extracted {row_count} rows"


def run_step_script(*, step: str, endpoint: Endpoint, fetch: Fetch, sql_dir: Path) -> None:
    """Entry point for a step script: parse the pipeline's arguments, run the step, and print the status line."""
    db_path, creds_file = arguments_loader(desc=f"Hadoop {step} extract")
    try:
        message = run_step(
            step=step,
            endpoint=endpoint,
            fetch=fetch,
            sql_dir=sql_dir,
            db_path=db_path,
            creds_file=creds_file,
        )
    except Exception as e:  # noqa: BLE001  (reported to the pipeline as a status line, then a non-zero exit)
        logger.error(f"Failed to extract {step}: {e}")
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)
    print(json.dumps({"status": "success", "message": message}))


def _write_table(*, step: str, sql_dir: Path, db_path: str, bodies: list[str], params: dict[str, str]) -> int:
    sql = (sql_dir / f"{step}.sql").read_text(encoding="utf-8").strip().rstrip(";")
    ddl = (sql_dir / f"{step}_ddl.sql").read_text(encoding="utf-8").strip()
    with connect_to_profiler_db(db_path) as conn:
        conn.begin()
        try:
            for macro in HELPER_MACROS:
                conn.execute(macro)
            conn.execute("CREATE TEMP TABLE raw_documents (doc JSON)")
            conn.register("_bodies", pd.DataFrame({"doc": bodies}))
            conn.execute("INSERT INTO raw_documents SELECT CAST(doc AS JSON) FROM _bodies")
            conn.unregister("_bodies")
            conn.execute(f"DROP TABLE IF EXISTS {step}")
            conn.execute(ddl)
            # DuckDB rejects named parameters the statement does not use, so bind only the ones it references.
            bound = {name: value for name, value in params.items() if f"${name}" in sql}
            conn.execute(f"INSERT INTO {step} SELECT * FROM ({sql})", bound or None)
            row_count = conn.execute(f"SELECT COUNT(*) FROM {step}").fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return int(row_count[0]) if row_count else 0
