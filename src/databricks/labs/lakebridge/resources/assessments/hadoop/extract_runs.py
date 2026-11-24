"""Initial and incremental runs, decided the way the original profiler decides them.

The original profiler runs an initial extract when ``ExtractTracker/initialrun.txt`` is absent. It writes that tracker
only at the end of an initial run, after the YARN ResourceManager check. A run that aborts writes no tracker, so the
next run is initial again. Every run after a completed initial extract is incremental: it reads only the recent history,
and skips the inventory that the initial extract already collected. Here the tracker is
``hadoop_extract_tracker.json`` in the output folder, next to the run's DuckDB file.

The tracker holds:

* ``initial_run_db``: the DuckDB file name of the run that is, or last was, doing the initial extract. Every step of that
  run finds the same answer, so the steps of one run agree on whether it is initial.
* ``initial_completed``: true once the run's final step (``hadoop_extract_tracker``) finds that this initial run's
  ``yarn_cluster_info`` succeeded, the equivalent of the original's check of the active ResourceManager.
* ``yarn_cluster_info_run_db``: the run whose ``yarn_cluster_info`` step last succeeded.
* ``completed_inventory_steps``: the inventory steps (see ``STEP_KINDS``) that have succeeded in the current initial
  extract. An incremental run skips these. An inventory step that has not succeeded yet runs again, which is how an
  initial extract that failed part way is finished by the next run.

A run is initial until ``initial_completed`` is true. A run that finds an unfinished initial extract by another run
starts the initial extract again from scratch. Deleting the tracker forces a new initial extract on the next run.
"""

import contextlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

TRACKER_FILE_NAME = "hadoop_extract_tracker.json"
SKIPPED_INCREMENTAL = "skipped: incremental run (initial extract already collected)"
# The step whose success decides that an initial extract is complete (the original's check of the active RM).
INITIAL_GATE_STEP = "yarn_cluster_info"
# The final step of a run. It writes no table; it only records the run in the tracker.
RECORD_STEP = "hadoop_extract_tracker"


class StepKind(Enum):
    """When a step runs: on every run, or on the initial run only (the inventory)."""

    EVERY_RUN = "every_run"
    INITIAL_ONLY = "initial_only"


_E = StepKind.EVERY_RUN
_I = StepKind.INITIAL_ONLY

# Every step of pipeline_config.yml, exactly once. Tests check this against the pipeline file.
STEP_KINDS: Mapping[str, StepKind] = {
    # YARN: the cluster identity and the application history run every time. The inventory is initial only.
    "yarn_cluster_info": _E,
    "yarn_applications": _E,
    "yarn_cluster_metrics": _I,
    "yarn_nodes": _I,
    "yarn_scheduler_queues": _I,
    # Spark History Server: the windows differ between the initial and incremental runs, so both steps run every time.
    "spark_applications": _E,
    "spark_application_attempts": _E,
    # Cloudera Manager: the version and cluster checks, the timeseries and the Impala queries run every time.
    "cm_clusters": _E,
    "cm_timeseries": _E,
    "cm_impala_queries": _E,
    "cm_services": _I,
    "cm_hosts": _I,
    "cm_host_config": _I,
    "cm_cluster_export": _I,
    # Ranger: the policies and the users, groups and service definitions are inventory.
    "ranger_services": _I,
    "ranger_service_defs": _I,
    "ranger_policies": _I,
    "ranger_policy_items": _I,
    "ranger_users": _I,
    "ranger_groups": _I,
    # Direct Impala: the query log is workload; the daemon, catalogd and statestore configuration is inventory.
    "impala_queries": _E,
    "impala_backends": _I,
    "impala_daemon_config": _I,
    "impala_catalog": _I,
    "impala_statestore": _I,
    # Ambari: the blueprint, hosts, services, components, stack and metrics are all inventory.
    "ambari_blueprint": _I,
    "ambari_blueprint_configurations": _I,
    "ambari_hosts": _I,
    "ambari_services": _I,
    "ambari_host_components": _I,
    "ambari_stack_versions": _I,
    "ambari_component_metrics": _I,
}


@dataclass(frozen=True)
class ExtractRun:
    """What a step needs to know about the run it belongs to."""

    run_db: str
    initial: bool
    completed: frozenset[str] = frozenset()


def resolve_run(db_path: str | Path) -> ExtractRun:
    """This run's kind. A run is initial until an initial extract completes. A run that starts while the last initial
    extract is unfinished takes it over and starts the initial extract again, as the original does after an abort."""
    path = Path(db_path)
    run_db = path.name
    tracker_path = path.parent / TRACKER_FILE_NAME
    tracker = _read_tracker(tracker_path)
    if tracker is None or (not tracker["initial_completed"] and tracker["initial_run_db"] != run_db):
        tracker = _fresh_tracker(run_db)
        _write_tracker(tracker_path, tracker)
    if not tracker["initial_completed"]:
        return ExtractRun(run_db=run_db, initial=True)
    return ExtractRun(run_db=run_db, initial=False, completed=frozenset(tracker["completed_inventory_steps"]))


def record_initial_gate(db_path: str | Path, run: ExtractRun) -> None:
    """Note that this run's yarn_cluster_info succeeded, so the run may complete its initial extract."""
    tracker_path = Path(db_path).parent / TRACKER_FILE_NAME
    tracker = _require_tracker(tracker_path, run)
    _write_tracker(tracker_path, {**tracker, "yarn_cluster_info_run_db": run.run_db})


def record_inventory_step(db_path: str | Path, run: ExtractRun, step: str) -> None:
    """Note that an inventory step has succeeded, so later incremental runs skip it."""
    tracker_path = Path(db_path).parent / TRACKER_FILE_NAME
    tracker = _require_tracker(tracker_path, run)
    completed = sorted(set(tracker["completed_inventory_steps"]) | {step})
    _write_tracker(tracker_path, {**tracker, "completed_inventory_steps": completed})


def finish_run(db_path: str | Path) -> str:
    """The run's final step: complete the initial extract if this run is initial and its yarn_cluster_info succeeded.

    Returns the status message. A run that is not initial, or whose initial extract is not complete, writes nothing
    unless it completes the initial extract now.
    """
    run_db = Path(db_path).name
    tracker_path = Path(db_path).parent / TRACKER_FILE_NAME
    tracker = _read_tracker(tracker_path)
    if tracker is None:
        return "nothing to record: no extract tracker"
    if tracker["initial_completed"] or tracker["initial_run_db"] != run_db:
        return "incremental run, nothing to record"
    if tracker.get("yarn_cluster_info_run_db") != run_db:
        return "initial extract not recorded: yarn_cluster_info did not succeed in this run"
    _write_tracker(tracker_path, {**tracker, "initial_completed": True})
    return "initial extract recorded"


def _fresh_tracker(run_db: str) -> dict[str, Any]:
    return {
        "initial_run_db": run_db,
        "initial_completed": False,
        "yarn_cluster_info_run_db": None,
        "completed_inventory_steps": [],
    }


def _require_tracker(tracker_path: Path, run: ExtractRun) -> dict[str, Any]:
    tracker = _read_tracker(tracker_path)
    if tracker is None:
        raise RuntimeError(f"{TRACKER_FILE_NAME} was removed during run {run.run_db}; the extract is not consistent")
    return tracker


def _read_tracker(tracker_path: Path) -> dict[str, Any] | None:
    try:
        text = tracker_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        tracker = json.loads(text)
        if not isinstance(tracker, dict) or not isinstance(tracker.get("initial_run_db"), str):
            raise ValueError("missing initial_run_db")
        steps = tracker.get("completed_inventory_steps", [])
        if not isinstance(steps, list) or not all(isinstance(step, str) for step in steps):
            raise ValueError("completed_inventory_steps must be a list of step names")
        completed = tracker.get("initial_completed", False)
        if not isinstance(completed, bool):
            raise ValueError("initial_completed must be true or false")
        gate = tracker.get("yarn_cluster_info_run_db")
        if gate is not None and not isinstance(gate, str):
            raise ValueError("yarn_cluster_info_run_db must be a file name or null")
        tracker = {**_fresh_tracker(tracker["initial_run_db"]), **tracker}
        tracker["completed_inventory_steps"] = steps
        tracker["initial_completed"] = completed
    except ValueError as e:
        raise RuntimeError(
            f"{tracker_path} is not a valid Hadoop extract tracker ({e}); delete it to force an initial extract"
        ) from e
    return tracker


def _write_tracker(tracker_path: Path, tracker: dict[str, Any]) -> None:
    """Write the tracker through a temporary file in the same folder, so a reader never sees a partial file."""
    fd, temp_name = tempfile.mkstemp(dir=tracker_path.parent, prefix=f".{TRACKER_FILE_NAME}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(tracker, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # mkstemp creates the file readable by its owner only; the other extract files are 0644.
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, tracker_path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise
