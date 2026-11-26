"""Shared runner for the Hadoop profiler steps.

A step fetches REST bodies, loads them as JSON documents into a temporary DuckDB table, runs ``<step>.sql`` over them and
writes the result as a table typed by ``<step>_ddl.sql`` (the SQL produces the columns in the DDL's order). It prints
one JSON status line last, for ``pipeline.py``.

The Impala steps are streamed one body at a time, so their SQL must work on one document. The direct Impala steps wrap
each body as ``{"coordinator": <base URL>, "body": ...}`` so rows name their daemon.
"""

import json
import sys
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
from databricks.labs.blueprint.entrypoint import get_logger

from databricks.labs.lakebridge.resources.assessments.common.cli import arguments_loader
from databricks.labs.lakebridge.resources.assessments.common.duckdb_helpers import connect_to_profiler_db
from databricks.labs.lakebridge.resources.assessments.hadoop.extract_runs import (
    INITIAL_GATE_STEP,
    SKIPPED_INCREMENTAL,
    STEP_KINDS,
    ExtractRun,
    StepKind,
    finish_run,
    record_initial_gate,
    record_inventory_step,
    resolve_run,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import (
    HadoopConfig,
    HadoopConfigError,
    ImpalaConfig,
    load_hadoop_config,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import (
    RestClient,
    find_active_resource_manager,
)

logger = get_logger(__file__)

Endpoint = Literal[
    "yarn",
    "spark_history",
    "cloudera_manager",
    "impala",
    "ranger",
    "impala_daemons",
    "impala_catalogd",
    "impala_statestored",
    "ambari",
]
# The Impala web UIs read directly (hadoop.impala), not through Cloudera Manager.
IMPALA_DIRECT_ENDPOINTS: tuple[str, ...] = ("impala_daemons", "impala_catalogd", "impala_statestored")
Fetch = Callable[[RestClient, HadoopConfig, ExtractRun], Iterable[str]]

# Every masked response body a step read, as the original profiler saved it, so nothing it kept is lost. Each step
# replaces its own rows in the same transaction as its table.
RAW_RESPONSES_TABLE = "hadoop_raw_responses"
RAW_RESPONSES_DDL = (
    f"CREATE TABLE IF NOT EXISTS {RAW_RESPONSES_TABLE} "
    "(step VARCHAR, sequence INTEGER, extracted_at TIMESTAMP, body JSON)"
)
# Steps that read the same response as an earlier step: they reuse its stored bodies from this run's extract instead
# of asking the service again, and fetch only when it stored none (it failed, or an incremental run skipped it).
SHARED_RESPONSE_STEPS: dict[str, str] = {
    "yarn_scheduler_queue_partitions": "yarn_scheduler_queues",
    "ranger_policy_items": "ranger_policies",
    "ambari_blueprint_configurations": "ambari_blueprint",
}

# TEMP macros, so they never persist into the extract. Each takes a JSON value.
HELPER_MACROS: tuple[str, ...] = (
    # The RM writes a list as an array, a single object, or "\n"/null when empty; anything else is an empty list.
    """CREATE TEMP MACRO hadoop_records(doc, path) AS (
        CASE json_type(doc, path)
            WHEN 'ARRAY' THEN CAST(json_extract(doc, path) AS JSON[])
            WHEN 'OBJECT' THEN [json_extract(doc, path)]
            ELSE CAST([] AS JSON[])
        END)""",
    # A YARN resource map as {"memory-mb":n,...}. The RM may repeat "entry" keys (a parser keeps only the last), so the
    # pairs are read from the text. Values may be quoted. NULL when absent.
    r"""CREATE TEMP MACRO yarn_resource_map(m) AS (
        CASE WHEN m IS NULL THEN NULL ELSE CAST(to_json(map_from_entries(list_transform(
            regexp_extract_all(CAST(m AS VARCHAR), '"key"\s*:\s*"[^"]*"\s*,\s*"value"\s*:\s*"?-?[0-9]+"?'),
            e -> {'k': regexp_extract(e, '"key"\s*:\s*"([^"]*)"', 1),
                  'v': CAST(regexp_extract(e, '"value"\s*:\s*"?(-?[0-9]+)', 1) AS BIGINT)}))) AS VARCHAR) END)""",
    # A Spark attempt time in epoch ms: <name>Epoch, or the text form when only that is reported. An unset time
    # (-1, or 1969-12-31T23:59:59.999GMT) becomes NULL.
    r"""CREATE TEMP MACRO spark_time_ms(att, name) AS (
        NULLIF(COALESCE(
            TRY_CAST(json_extract_string(att, '$.' || name || 'Epoch') AS BIGINT),
            epoch_ms(try_strptime(regexp_replace(json_extract_string(att, '$.' || name), 'GMT$', ''),
                                  '%Y-%m-%dT%H:%M:%S.%g'))
        ), -1))""",
    # A queue's children: queues.queue (capacity scheduler), or childQueues, wrapped as childQueues.queue or bare (fair).
    """CREATE TEMP MACRO child_queues(node) AS (
        CASE
            WHEN json_type(node, '$.queues.queue') IS NOT NULL THEN hadoop_records(node, '$.queues.queue')
            WHEN json_type(node, '$.childQueues.queue') IS NOT NULL THEN hadoop_records(node, '$.childQueues.queue')
            ELSE hadoop_records(node, '$.childQueues')
        END)""",
    # Impala prints up to nanoseconds; DuckDB reads microseconds, so the rest is cut. 1970-01-01 00:00:00 means unset.
    r"""CREATE TEMP MACRO impala_timestamp(s) AS (
        CASE
            WHEN s IS NULL OR trim(s) = '' OR trim(s) LIKE '1970-01-01 00:00:00%' THEN NULL
            ELSE TRY_CAST(regexp_replace(trim(s), '(\.[0-9]{6})[0-9]+$', '\1') AS TIMESTAMP)
        END)""",
    # Impala's compound durations ("257.995ms", "1s156ms", "1m5s") in ms. A bare number, such as "0", is ms.
    r"""CREATE TEMP MACRO impala_duration_ms(s) AS (
        CASE
            WHEN s IS NULL OR trim(s) = '' THEN NULL
            WHEN regexp_full_match(trim(s), '[0-9]+(\.[0-9]+)?') THEN CAST(trim(s) AS DOUBLE)
            WHEN regexp_matches(trim(s), '^(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+(?:\.[0-9]+)?)s)?(?:([0-9]+(?:\.[0-9]+)?)ms)?(?:([0-9]+(?:\.[0-9]+)?)us)?(?:([0-9]+(?:\.[0-9]+)?)ns)?$')
            THEN
                COALESCE(TRY_CAST(NULLIF(regexp_extract(trim(s), '^(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+(?:\.[0-9]+)?)s)?(?:([0-9]+(?:\.[0-9]+)?)ms)?(?:([0-9]+(?:\.[0-9]+)?)us)?(?:([0-9]+(?:\.[0-9]+)?)ns)?$', 1), '') AS DOUBLE), 0) * 3600000
              + COALESCE(TRY_CAST(NULLIF(regexp_extract(trim(s), '^(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+(?:\.[0-9]+)?)s)?(?:([0-9]+(?:\.[0-9]+)?)ms)?(?:([0-9]+(?:\.[0-9]+)?)us)?(?:([0-9]+(?:\.[0-9]+)?)ns)?$', 2), '') AS DOUBLE), 0) * 60000
              + COALESCE(TRY_CAST(NULLIF(regexp_extract(trim(s), '^(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+(?:\.[0-9]+)?)s)?(?:([0-9]+(?:\.[0-9]+)?)ms)?(?:([0-9]+(?:\.[0-9]+)?)us)?(?:([0-9]+(?:\.[0-9]+)?)ns)?$', 3), '') AS DOUBLE), 0) * 1000
              + COALESCE(TRY_CAST(NULLIF(regexp_extract(trim(s), '^(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+(?:\.[0-9]+)?)s)?(?:([0-9]+(?:\.[0-9]+)?)ms)?(?:([0-9]+(?:\.[0-9]+)?)us)?(?:([0-9]+(?:\.[0-9]+)?)ns)?$', 4), '') AS DOUBLE), 0)
              + COALESCE(TRY_CAST(NULLIF(regexp_extract(trim(s), '^(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+(?:\.[0-9]+)?)s)?(?:([0-9]+(?:\.[0-9]+)?)ms)?(?:([0-9]+(?:\.[0-9]+)?)us)?(?:([0-9]+(?:\.[0-9]+)?)ns)?$', 5), '') AS DOUBLE), 0) / 1000
              + COALESCE(TRY_CAST(NULLIF(regexp_extract(trim(s), '^(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+(?:\.[0-9]+)?)s)?(?:([0-9]+(?:\.[0-9]+)?)ms)?(?:([0-9]+(?:\.[0-9]+)?)us)?(?:([0-9]+(?:\.[0-9]+)?)ns)?$', 6), '') AS DOUBLE), 0) / 1000000
            ELSE NULL
        END)""",
    # Impala's binary-unit sizes ("4.11 MB", "205.00 B", "0") in bytes.
    r"""CREATE TEMP MACRO impala_bytes(s) AS (
        CASE
            WHEN s IS NULL OR trim(s) = '' THEN NULL
            WHEN regexp_matches(trim(s), '^[0-9]+(\.[0-9]+)?\s*(B|KB|MB|GB|TB)?$') THEN CAST(ROUND(
                CAST(regexp_extract(trim(s), '^([0-9.]+)', 1) AS DOUBLE) * CASE regexp_extract(trim(s), '([KMGT]?B)$', 1)
                    WHEN 'KB' THEN 1024
                    WHEN 'MB' THEN 1048576
                    WHEN 'GB' THEN 1073741824
                    WHEN 'TB' THEN 1099511627776
                    ELSE 1
                END) AS BIGINT)
            ELSE NULL
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
    """Extract one step and return its status message; raises on failure.

    An incremental run skips an inventory step (``STEP_KINDS``) that already succeeded; a success is recorded.
    """
    run = resolve_run(db_path)
    kind = STEP_KINDS[step]
    if kind is StepKind.INITIAL_ONLY and not run.initial and step in run.completed:
        return SKIPPED_INCREMENTAL
    message, extracted = _extract(
        step=step,
        endpoint=endpoint,
        fetch=fetch,
        sql_dir=sql_dir,
        db_path=db_path,
        creds_file=creds_file,
        run=run,
    )
    if extracted and kind is StepKind.INITIAL_ONLY:
        record_inventory_step(db_path, run, step)
    if extracted and run.initial and step == INITIAL_GATE_STEP:
        record_initial_gate(db_path, run)
    return message


def _extract(
    *,
    step: str,
    endpoint: Endpoint,
    fetch: Fetch,
    sql_dir: Path,
    db_path: str,
    creds_file: Path,
    run: ExtractRun,
) -> tuple[str, bool]:
    """Fetch and write the step. Returns its status message and whether it wrote a table."""
    config = load_hadoop_config(creds_file)
    source = SHARED_RESPONSE_STEPS.get(step)
    shared = stored_bodies(db_path, source) if source else []
    if source and shared:
        # Already masked by the source step.
        row_count = write_table(
            step=step,
            sql_dir=sql_dir,
            db_path=db_path,
            bodies=shared,
            params={"distribution": config.distribution},
        )
        return f"{step} extracted {row_count} rows (from the {source} response)", True
    if endpoint in IMPALA_DIRECT_ENDPOINTS:
        clients = _impala_clients(endpoint, config.impala)
        if isinstance(clients, str):
            return clients, False
        # Wrapped with the daemon's URL, then masked as a whole so the rules also reach that URL.
        bodies = (
            config.mask(coordinator_envelope(client.base_url, body))
            for client in clients
            for body in fetch(client, config, run)
        )
        row_count = write_table(
            step=step,
            sql_dir=sql_dir,
            db_path=db_path,
            bodies=bodies,
            params={"distribution": config.distribution},
            streaming=True,
        )
        return f"{step} extracted {row_count} rows", True
    if endpoint == "yarn":
        if config.yarn is None:
            raise HadoopConfigError("hadoop.yarn is not configured; the YARN steps need it")
        client = find_active_resource_manager(config.yarn.urls, make_client=config.yarn.client)
    elif endpoint == "spark_history":
        if config.spark_history is None:
            return "skipped: not configured (hadoop.spark_history is absent)", False
        client = config.spark_history.client(config.spark_history.urls[0])
    elif endpoint == "ambari":
        if config.ambari is None:
            return "skipped: not configured (hadoop.ambari is absent)", False
        client = config.ambari.endpoint.client(config.ambari.endpoint.urls[0])
    elif endpoint == "ranger":
        if config.ranger is None:
            return "skipped: not configured (hadoop.ranger is absent)", False
        client = config.ranger.client(config.ranger.urls[0])
    else:
        cm = config.cloudera_manager
        if cm is None:
            return "skipped: not configured (hadoop.cloudera_manager is absent)", False
        if endpoint == "impala" and not cm.extract_impala_queries:
            return "skipped: not configured (hadoop.cloudera_manager.extract_impala_queries is false)", False
        client = cm.endpoint.client(cm.endpoint.urls[0])

    # Masked before parsing, as the original's SEARCH_REPLACE pipe.
    bodies = (config.mask(body) for body in fetch(client, config, run))
    row_count = write_table(
        step=step,
        sql_dir=sql_dir,
        db_path=db_path,
        bodies=bodies,
        params={"distribution": config.distribution},
        streaming=endpoint == "impala",
    )
    return f"{step} extracted {row_count} rows", True


def _impala_clients(endpoint: str, impala: ImpalaConfig | None) -> list[RestClient] | str:
    """The clients an Impala step reads, or why the step is skipped."""
    if impala is None:
        return "skipped: not configured (hadoop.impala is absent)"
    if endpoint == "impala_daemons":
        return [impala.endpoint.client(url) for url in impala.endpoint.urls]
    if endpoint == "impala_catalogd":
        url, key = impala.catalogd_url, "catalogd_url"
    else:
        url, key = impala.statestored_url, "statestored_url"
    if url is None:
        return f"skipped: not configured (hadoop.impala.{key} is absent)"
    return [impala.endpoint.client(url)]


def coordinator_envelope(base_url: str, body: str) -> str:
    return '{"coordinator":' + json.dumps(base_url) + ',"body":' + body + "}"


def run_step_script(*, step: str, endpoint: Endpoint, fetch: Fetch, sql_dir: Path) -> None:
    """Step script entry point: run the step and print its status line."""
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


def run_finish_script() -> None:
    """The run's final step: record the run in the tracker and print the status line."""
    db_path, _creds_file = arguments_loader(desc="Hadoop extract tracker")
    try:
        message = finish_run(db_path)
    except Exception as e:  # noqa: BLE001  (reported to the pipeline as a status line, then a non-zero exit)
        logger.error(f"Failed to record the Hadoop extract run: {e}")
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)
    print(json.dumps({"status": "success", "message": message}))


def stored_bodies(db_path: str, step: str) -> list[str]:
    """The bodies ``step`` stored in this extract's ``hadoop_raw_responses``, in order."""
    with connect_to_profiler_db(db_path) as conn:
        exists = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [RAW_RESPONSES_TABLE]
        ).fetchone()
        if not exists or not exists[0]:
            return []
        rows = conn.execute(
            f"SELECT CAST(body AS VARCHAR) FROM {RAW_RESPONSES_TABLE} WHERE step = ? ORDER BY sequence", [step]
        ).fetchall()
    return [row[0] for row in rows]


def write_table(
    *,
    step: str,
    sql_dir: Path,
    db_path: str,
    bodies: Iterable[str],
    params: dict[str, str],
    streaming: bool = False,
) -> int:
    sql = (sql_dir / f"{step}.sql").read_text(encoding="utf-8").strip().rstrip(";")
    ddl = (sql_dir / f"{step}_ddl.sql").read_text(encoding="utf-8").strip()
    # DuckDB rejects unused named parameters.
    bound = {name: value for name, value in params.items() if f"${name}" in sql} or None
    with connect_to_profiler_db(db_path) as conn:
        conn.begin()
        try:
            for macro in HELPER_MACROS:
                conn.execute(macro)
            conn.execute("CREATE TEMP TABLE raw_documents (doc JSON)")
            conn.execute(f"DROP TABLE IF EXISTS {step}")
            conn.execute(ddl)
            conn.execute(RAW_RESPONSES_DDL)
            conn.execute(f"DELETE FROM {RAW_RESPONSES_TABLE} WHERE step = ?", [step])
            extracted_at = datetime.now(timezone.utc).replace(tzinfo=None)
            if streaming:
                for sequence, body in enumerate(bodies, start=1):
                    conn.execute("DELETE FROM raw_documents")
                    conn.execute("INSERT INTO raw_documents VALUES (CAST(? AS JSON))", [body])
                    conn.execute(
                        f"INSERT INTO {RAW_RESPONSES_TABLE} SELECT ?, ?, ?, doc FROM raw_documents",
                        [step, sequence, extracted_at],
                    )
                    conn.execute(f"INSERT INTO {step} SELECT * FROM ({sql})", bound)
            else:
                docs = list(bodies)
                conn.register("_bodies", pd.DataFrame({"sequence": range(1, len(docs) + 1), "doc": docs}))
                conn.execute("INSERT INTO raw_documents SELECT CAST(doc AS JSON) FROM _bodies ORDER BY sequence")
                conn.execute(
                    f"INSERT INTO {RAW_RESPONSES_TABLE} "
                    "SELECT ?, CAST(sequence AS INTEGER), ?, CAST(doc AS JSON) FROM _bodies ORDER BY sequence",
                    [step, extracted_at],
                )
                conn.unregister("_bodies")
                conn.execute(f"INSERT INTO {step} SELECT * FROM ({sql})", bound)
            # Optional work across all bodies, such as one row per query when a query is on several pages.
            after_load = sql_dir / f"{step}_after_load.sql"
            if after_load.exists():
                conn.execute(after_load.read_text(encoding="utf-8"))
            row_count = conn.execute(f"SELECT COUNT(*) FROM {step}").fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return int(row_count[0]) if row_count else 0
