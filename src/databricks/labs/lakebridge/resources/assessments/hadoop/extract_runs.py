"""Initial and incremental runs, decided as the original profiler decides them.

As with the original's ``ExtractTracker/initialrun.txt``, the initial extract is recorded only when a run completes it
after the ResourceManager check; until then every run is initial. Later runs are incremental: recent history only, and
no inventory already collected. The tracker is ``hadoop_extract_tracker.json`` next to the run's DuckDB file:

* ``initial_run_db``: the file of the run doing, or last doing, the initial extract, so a run's steps agree.
* ``initial_completed``: set by the final step when this initial run's ``yarn_cluster_info`` succeeded.
* ``yarn_cluster_info_run_db``: the run whose ``yarn_cluster_info`` last succeeded.
* ``completed_inventory_steps``: inventory steps (``STEP_KINDS``) done in the initial extract; incremental runs skip
  them, and the rest run again, which finishes a partly failed initial extract.

A run that finds another run's unfinished initial extract starts it again. Deleting the tracker forces a new one.
A run records into the tracker only while it is the one it resolved (same ``initial_run_db``, same completion), so
overlapping runs in one folder cannot credit a step to a DuckDB file that does not hold its data. Each read and update
of the tracker holds an operating-system lock on ``hadoop_extract_tracker.json.lock``, so a scheduled run and a manual
one cannot interleave them; the system releases the lock when a process ends, so a crashed run leaves no lock behind.
The lock file is kept: removing it while another run holds it would let a third run lock a new file.
"""

import contextlib
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

TRACKER_FILE_NAME = "hadoop_extract_tracker.json"
LOCK_FILE_NAME = f"{TRACKER_FILE_NAME}.lock"
# A run holds the lock only to read and update the tracker, so a long wait means a stuck process.
LOCK_TIMEOUT_SECONDS = 60.0
_LOCK_RETRY_SECONDS = 0.05
SKIPPED_INCREMENTAL = "skipped: incremental run (initial extract already collected)"
# Its success completes the initial extract (the original's active-RM check).
INITIAL_GATE_STEP = "yarn_cluster_info"
# A run's final step: it writes no table, only the tracker.
RECORD_STEP = "hadoop_extract_tracker"


class StepKind(Enum):
    """When a step runs: every run, or the initial run only (inventory)."""

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
    "yarn_scheduler_queue_partitions": _I,
    # Spark History Server: every run, with a shorter window on incremental runs.
    "spark_applications": _E,
    "spark_application_attempts": _E,
    # Cloudera Manager: clusters, timeseries and Impala queries run every time.
    "cm_clusters": _E,
    "cm_timeseries": _E,
    "cm_impala_queries": _E,
    "cm_services": _I,
    "cm_hosts": _I,
    "cm_host_config": _I,
    "cm_cluster_export": _I,
    # Ranger: the services and policies are inventory.
    "ranger_services": _I,
    "ranger_policies": _I,
    "ranger_policy_items": _I,
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
    # The tracker's initial_run_db when this run resolved it; a run records only while it is unchanged.
    tracker_owner: str | None = None


def resolve_run(db_path: str | Path) -> ExtractRun:
    """This run's kind. A run that finds an unfinished initial extract starts it again, as the original does."""
    path = Path(db_path)
    run_db = path.name
    tracker_path = path.parent / TRACKER_FILE_NAME
    with _tracker_lock(tracker_path):
        tracker = _read_tracker(tracker_path)
        if tracker is None or (not tracker["initial_completed"] and tracker["initial_run_db"] != run_db):
            tracker = _fresh_tracker(run_db)
            _write_tracker(tracker_path, tracker)
    owner = tracker["initial_run_db"]
    if not tracker["initial_completed"]:
        return ExtractRun(run_db=run_db, initial=True, tracker_owner=owner)
    completed = frozenset(tracker["completed_inventory_steps"])
    return ExtractRun(run_db=run_db, initial=False, completed=completed, tracker_owner=owner)


def record_initial_gate(db_path: str | Path, run: ExtractRun) -> None:
    """Record that this run's yarn_cluster_info succeeded."""
    tracker_path = Path(db_path).parent / TRACKER_FILE_NAME
    with _tracker_lock(tracker_path):
        tracker = _require_tracker(tracker_path, run, "yarn_cluster_info")
        if tracker is None:
            return
        _write_tracker(tracker_path, {**tracker, "yarn_cluster_info_run_db": run.run_db})


def record_inventory_step(db_path: str | Path, run: ExtractRun, step: str) -> None:
    """Record an inventory step's success, so incremental runs skip it."""
    tracker_path = Path(db_path).parent / TRACKER_FILE_NAME
    with _tracker_lock(tracker_path):
        tracker = _require_tracker(tracker_path, run, step)
        if tracker is None:
            return
        completed = sorted(set(tracker["completed_inventory_steps"]) | {step})
        _write_tracker(tracker_path, {**tracker, "completed_inventory_steps": completed})


def finish_run(db_path: str | Path) -> str:
    """The final step: mark the initial extract complete if this initial run's yarn_cluster_info succeeded.

    Returns the status message; any other run writes nothing.
    """
    run_db = Path(db_path).name
    tracker_path = Path(db_path).parent / TRACKER_FILE_NAME
    with _tracker_lock(tracker_path):
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


def _require_tracker(tracker_path: Path, run: ExtractRun, step: str) -> dict[str, Any] | None:
    """The tracker this run resolved, or None when another run has since replaced it (nothing is recorded then)."""
    tracker = _read_tracker(tracker_path)
    if tracker is None:
        raise RuntimeError(f"{TRACKER_FILE_NAME} was removed during run {run.run_db}; the extract is not consistent")
    if tracker["initial_run_db"] != run.tracker_owner or tracker["initial_completed"] is run.initial:
        logger.warning(
            f"{step} is not recorded in {TRACKER_FILE_NAME}: another run ({tracker['initial_run_db']}) replaced the "
            f"tracker during run {run.run_db}; its data stays in {run.run_db}"
        )
        return None
    return tracker


@contextlib.contextmanager
def _tracker_lock(tracker_path: Path) -> Iterator[None]:
    """Hold the folder's tracker lock, waiting up to ``LOCK_TIMEOUT_SECONDS`` for another run to release it."""
    lock_path = tracker_path.parent / LOCK_FILE_NAME
    # Read-only is enough to lock, so a run can share a lock file another user created.
    fd = os.open(lock_path, os.O_RDONLY | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"{lock_path} has been locked by another profiler run for {LOCK_TIMEOUT_SECONDS:.0f}s; "
                    "check for a stuck run in this folder"
                )
            time.sleep(_LOCK_RETRY_SECONDS)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


if sys.platform == "win32":

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


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
    """Write the tracker atomically, through a temporary file in the same folder."""
    fd, temp_name = tempfile.mkstemp(dir=tracker_path.parent, prefix=f".{TRACKER_FILE_NAME}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(tracker, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # mkstemp makes it owner-only; match the extract's 0644.
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, tracker_path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise
