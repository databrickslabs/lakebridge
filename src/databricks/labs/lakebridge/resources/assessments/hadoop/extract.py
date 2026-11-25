"""Shared runner for the Hadoop profiler steps.

A step script fetches REST bodies, loads them into a temporary DuckDB table of JSON documents, runs its own
``<step>.sql`` over them, and writes the result into the profiler extract as a table typed by ``<step>_ddl.sql``.
It prints one JSON status line last, which ``pipeline.py`` reads.

Column names and types come from the DDL, so the SQL only has to produce the columns in the DDL's order.

The Impala extracts are written one body at a time: their SQL must then produce rows from one document on its own. The
direct Impala extracts (``impala_daemons``, ``impala_catalogd``, ``impala_statestored``) wrap each body in a
``{"coordinator": <base URL>, "body": ...}`` envelope, so the rows say which daemon they came from. The Cloudera Manager
Impala extract is large, so it is written the same way. Every other step reads all its bodies at once.
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
# The Impala daemon web UIs, read directly (hadoop.impala), as opposed to the Cloudera Manager "impala" endpoint.
IMPALA_DIRECT_ENDPOINTS: tuple[str, ...] = ("impala_daemons", "impala_catalogd", "impala_statestored")
Fetch = Callable[[RestClient, HadoopConfig, ExtractRun], Iterable[str]]

# Every REST response body a step read, after masking, exactly as the original profiler saved it to its Output files.
# The typed step tables keep the fields the analysis uses; this table keeps the rest, so nothing the original kept is
# lost. Each step replaces its own rows, in the same transaction as its typed table.
RAW_RESPONSES_TABLE = "hadoop_raw_responses"
RAW_RESPONSES_DDL = (
    f"CREATE TABLE IF NOT EXISTS {RAW_RESPONSES_TABLE} "
    "(step VARCHAR, sequence INTEGER, extracted_at TIMESTAMP, body JSON)"
)

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
    # A YARN resource map (resourceSecondsMap, preemptedResourceSecondsMap) as a JSON object {"memory-mb":n,...}. The RM
    # writes the entries as repeated "entry" keys in one object, which a JSON parser collapses to the last, or as an
    # array, so the pairs are read from the text. Values may be quoted. NULL when the map is absent.
    r"""CREATE TEMP MACRO yarn_resource_map(m) AS (
        CASE WHEN m IS NULL THEN NULL ELSE CAST(to_json(map_from_entries(list_transform(
            regexp_extract_all(CAST(m AS VARCHAR), '"key"\s*:\s*"[^"]*"\s*,\s*"value"\s*:\s*"?-?[0-9]+"?'),
            e -> {'k': regexp_extract(e, '"key"\s*:\s*"([^"]*)"', 1),
                  'v': CAST(regexp_extract(e, '"value"\s*:\s*"?(-?[0-9]+)', 1) AS BIGINT)}))) AS VARCHAR) END)""",
    # A Spark History Server attempt time in epoch milliseconds: <name>Epoch, or the text form (such as
    # 2026-11-25T10:39:13.015GMT) when the server reports only that. A time that is not set yet is -1 either way
    # (1969-12-31T23:59:59.999GMT in text), which becomes NULL.
    r"""CREATE TEMP MACRO spark_time_ms(att, name) AS (
        NULLIF(COALESCE(
            TRY_CAST(json_extract_string(att, '$.' || name || 'Epoch') AS BIGINT),
            epoch_ms(try_strptime(regexp_replace(json_extract_string(att, '$.' || name), 'GMT$', ''),
                                  '%Y-%m-%dT%H:%M:%S.%g'))
        ), -1))""",
    # A scheduler queue's children: capacity schedulers nest them under queues.queue; fair schedulers under
    # childQueues, which Hadoop 2.8+ wraps as childQueues.queue (FairSchedulerQueueInfoList) and older YARN gives bare.
    """CREATE TEMP MACRO child_queues(node) AS (
        CASE
            WHEN json_type(node, '$.queues.queue') IS NOT NULL THEN hadoop_records(node, '$.queues.queue')
            WHEN json_type(node, '$.childQueues.queue') IS NOT NULL THEN hadoop_records(node, '$.childQueues.queue')
            ELSE hadoop_records(node, '$.childQueues')
        END)""",
    # Impala's web UI prints a timestamp with up to nanoseconds ("2026-11-24 05:52:53.851880000"). DuckDB reads
    # microseconds, so the extra digits are cut. Impala reports 1970-01-01 00:00:00 for a time that is not set yet.
    r"""CREATE TEMP MACRO impala_timestamp(s) AS (
        CASE
            WHEN s IS NULL OR trim(s) = '' OR trim(s) LIKE '1970-01-01 00:00:00%' THEN NULL
            ELSE TRY_CAST(regexp_replace(trim(s), '(\.[0-9]{6})[0-9]+$', '\1') AS TIMESTAMP)
        END)""",
    # Impala's durations are compound and use the largest units that fit: "257.995ms", "1s156ms", "7s039ms", "0.000ns",
    # "1m5s". A bare number (Impala prints "0" for zero) is taken as milliseconds. The result is in milliseconds.
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
    # Impala's sizes use binary units: "4.11 MB", "1.44 KB", "205.00 B", "0". The result is in bytes.
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
    """Extract one step and return its status message. Raises on failure.

    The step first asks the run tracker whether this run is the initial one. An inventory step (``STEP_KINDS``) that
    already succeeded in the initial run is skipped by an incremental run. Once an inventory step succeeds, it is recorded
    so that later runs skip it.
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
    """Fetch and write the step. Returns its status message and whether it wrote a table (a skip does not)."""
    config = load_hadoop_config(creds_file)
    if endpoint in IMPALA_DIRECT_ENDPOINTS:
        clients = _impala_clients(endpoint, config.impala)
        if isinstance(clients, str):
            return clients, False
        # Each daemon's body is wrapped with the daemon's URL so the step's rows say where they came from, then masked
        # as a whole, so the masking rules also apply to the coordinator URL.
        bodies = (
            config.mask(_coordinator_envelope(client.base_url, body))
            for client in clients
            for body in fetch(client, config, run)
        )
        row_count = _write_table(
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
        ranger = config.ranger
        client = RestClient(ranger.urls[0], username=ranger.username, password=ranger.password, verify=ranger.verify)
    else:
        cm = config.cloudera_manager
        if cm is None:
            return "skipped: not configured (hadoop.cloudera_manager is absent)", False
        if endpoint == "impala" and not cm.extract_impala_queries:
            return "skipped: not configured (hadoop.cloudera_manager.extract_impala_queries is false)", False
        client = RestClient(
            cm.endpoint.urls[0],
            username=cm.endpoint.username,
            password=cm.endpoint.password,
            verify=cm.endpoint.verify,
        )

    # Masking runs on the raw responses before parsing, as the original profiler's SEARCH_REPLACE pipe did.
    bodies = (config.mask(body) for body in fetch(client, config, run))
    row_count = _write_table(
        step=step,
        sql_dir=sql_dir,
        db_path=db_path,
        bodies=bodies,
        params={"distribution": config.distribution},
        streaming=endpoint == "impala",
    )
    return f"{step} extracted {row_count} rows", True


def _impala_clients(endpoint: str, impala: ImpalaConfig | None) -> list[RestClient] | str:
    """The clients an Impala step reads, or the message that says why the step is skipped."""
    if impala is None:
        return "skipped: not configured (hadoop.impala is absent)"
    if endpoint == "impala_daemons":
        return [_impala_client(url, impala) for url in impala.endpoint.urls]
    if endpoint == "impala_catalogd":
        url, key = impala.catalogd_url, "catalogd_url"
    else:
        url, key = impala.statestored_url, "statestored_url"
    if url is None:
        return f"skipped: not configured (hadoop.impala.{key} is absent)"
    return [_impala_client(url, impala)]


def _impala_client(url: str, impala: ImpalaConfig) -> RestClient:
    return RestClient(
        url,
        username=impala.endpoint.username,
        password=impala.endpoint.password,
        verify=impala.endpoint.verify,
    )


def _coordinator_envelope(base_url: str, body: str) -> str:
    return '{"coordinator":' + json.dumps(base_url) + ',"body":' + body + "}"


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


def run_finish_script() -> None:
    """Entry point for the run's final step: record the run in the tracker and print the status line."""
    db_path, _creds_file = arguments_loader(desc="Hadoop extract tracker")
    try:
        message = finish_run(db_path)
    except Exception as e:  # noqa: BLE001  (reported to the pipeline as a status line, then a non-zero exit)
        logger.error(f"Failed to record the Hadoop extract run: {e}")
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)
    print(json.dumps({"status": "success", "message": message}))


def _write_table(
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
    # DuckDB rejects named parameters the statement does not use, so bind only the ones it references.
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
            # A step can finish its table once every body is loaded, for work that spans bodies (streamed bodies are
            # loaded one at a time), such as keeping one row per query when a query is on several pages.
            after_load = sql_dir / f"{step}_after_load.sql"
            if after_load.exists():
                conn.execute(after_load.read_text(encoding="utf-8"))
            row_count = conn.execute(f"SELECT COUNT(*) FROM {step}").fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return int(row_count[0]) if row_count else 0
