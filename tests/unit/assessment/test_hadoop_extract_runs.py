"""Initial and incremental runs: the run tracker, the completion of an initial extract, the step classification, and the
windows each run reads."""

import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Callable, Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from databricks.labs.lakebridge.assessments.pipeline import PipelineClass
from databricks.labs.lakebridge.resources.assessments.hadoop import endpoints, extract, extract_runs
from databricks.labs.lakebridge.resources.assessments.hadoop.extract_runs import (
    LOCK_FILE_NAME,
    RECORD_STEP,
    SKIPPED_INCREMENTAL,
    STEP_KINDS,
    TRACKER_FILE_NAME,
    ExtractRun,
    StepKind,
    finish_run,
    record_initial_gate,
    record_inventory_step,
    resolve_run,
)
from databricks.labs.lakebridge.resources.assessments.hadoop.hadoop_config import parse_hadoop_config
from databricks.labs.lakebridge.resources.assessments.hadoop.rest_client import RestClient

_HADOOP = Path(extract.__file__).resolve().parent
_FIXTURES = Path(__file__).resolve().parents[2] / "resources" / "assessments" / "hadoop"
_PIPELINE = _HADOOP / "pipeline_config.yml"
_NOW = datetime(2026, 11, 24, 2, 30, tzinfo=timezone.utc)


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


class _FixedDatetime(datetime):
    """The endpoints' clock, pinned to 2026-11-24 02:30 UTC."""

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return _NOW if tz is None else _NOW.astimezone(tz)


@pytest.fixture(name="fixed_clock")
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(endpoints, "datetime", _FixedDatetime)


@pytest.fixture(autouse=True)
def _reachable_resource_manager(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The YARN steps find an active ResourceManager; these tests fake one, so no network is needed."""
    monkeypatch.setattr(extract, "find_active_resource_manager", lambda urls, make_client: object())
    yield


def _tracker(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / TRACKER_FILE_NAME).read_text(encoding="utf-8"))


def _db(tmp_path: Path, name: str) -> str:
    return str(tmp_path / name)


def _creds(tmp_path: Path) -> Path:
    creds = tmp_path / ".credentials.yml"
    creds.write_text(
        yaml.safe_dump(
            {
                "secret_vault_type": "local",
                "hadoop": {
                    "distribution": "hdp",
                    "yarn": {"urls": ["http://rm:8088"]},
                    "spark_history": {"url": "http://spark:18081"},
                    "ambari": {"url": "http://ambari:8080", "username": "admin", "password": "pw"},
                },
            }
        ),
        encoding="utf-8",
    )
    return creds


def _run(tmp_path: Path, db_name: str, step: str, endpoint: str, fetch: Callable[..., list[str]]) -> str:
    return extract.run_step(
        step=step,
        endpoint=endpoint,  # type: ignore[arg-type]  # a literal endpoint name
        fetch=fetch,
        sql_dir=_HADOOP,
        db_path=_db(tmp_path, db_name),
        creds_file=_creds(tmp_path),
    )


def _yarn_ok(tmp_path: Path, db_name: str) -> str:
    return _run(tmp_path, db_name, "yarn_cluster_info", "yarn", lambda *_: [_fixture("cluster_info_active.json")])


def _yarn_down(tmp_path: Path, db_name: str) -> None:
    def down(*_: Any) -> list[str]:
        raise RuntimeError("Connection refused: 127.0.0.1:59999")

    with pytest.raises(RuntimeError, match="Connection refused"):
        _run(tmp_path, db_name, "yarn_cluster_info", "yarn", down)


def _complete_initial(tmp_path: Path, db_name: str) -> None:
    """A whole initial run that reaches its end: yarn_cluster_info succeeds, then the final step records the run."""
    _yarn_ok(tmp_path, db_name)
    assert finish_run(_db(tmp_path, db_name)) == "initial extract recorded"


def _ambari_hosts(calls: list[str]) -> Callable[..., list[str]]:
    def fetch(_client: RestClient, _config: Any, _run: ExtractRun) -> list[str]:
        calls.append("ambari_hosts")
        return [_fixture("ambari_hosts_sim.json")]

    return fetch


# ---- the tracker and the run kind ----------------------------------------------------------------------------------


def test_first_run_with_no_tracker_is_initial_and_creates_the_tracker(tmp_path: Path) -> None:
    run = resolve_run(_db(tmp_path, "profiler_extract_a.db"))

    assert run.initial is True
    assert _tracker(tmp_path) == {
        "completed_inventory_steps": [],
        "initial_completed": False,
        "initial_run_db": "profiler_extract_a.db",
        "yarn_cluster_info_run_db": None,
    }


def test_steps_of_the_same_run_agree_on_its_kind(tmp_path: Path) -> None:
    assert resolve_run(_db(tmp_path, "run_a.db")).initial
    assert resolve_run(_db(tmp_path, "run_a.db")).initial


def test_a_run_stays_initial_until_its_initial_extract_completes(tmp_path: Path) -> None:
    resolve_run(_db(tmp_path, "run_a.db"))

    # The first run aborted before its end, so the next run starts the initial extract again.
    later = resolve_run(_db(tmp_path, "run_b.db"))

    assert later.initial is True
    assert _tracker(tmp_path)["initial_run_db"] == "run_b.db"


def test_a_completed_initial_extract_makes_the_later_runs_incremental(tmp_path: Path) -> None:
    _complete_initial(tmp_path, "run_a.db")

    later = resolve_run(_db(tmp_path, "run_b.db"))

    assert later.initial is False
    assert _tracker(tmp_path)["initial_completed"] is True


def test_recording_an_inventory_step_is_written_once_and_sorted(tmp_path: Path) -> None:
    run_db = _db(tmp_path, "run_a.run_db")
    run = resolve_run(run_db)

    record_inventory_step(run_db, run, "ambari_hosts")
    record_inventory_step(run_db, run, "ambari_blueprint")
    record_inventory_step(run_db, run, "ambari_hosts")

    assert _tracker(tmp_path)["completed_inventory_steps"] == ["ambari_blueprint", "ambari_hosts"]


def test_deleting_the_tracker_forces_a_new_initial_run(tmp_path: Path) -> None:
    _complete_initial(tmp_path, "run_a.db")
    assert resolve_run(_db(tmp_path, "run_b.db")).initial is False

    (tmp_path / TRACKER_FILE_NAME).unlink()

    assert resolve_run(_db(tmp_path, "run_c.db")).initial is True


def test_the_tracker_is_written_atomically_and_a_failed_write_keeps_the_old_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = _db(tmp_path, "run_a.db")
    run = resolve_run(db_file)
    before = (tmp_path / TRACKER_FILE_NAME).read_text(encoding="utf-8")

    def fail(_src: str, _dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        record_inventory_step(db_file, run, "ambari_hosts")

    assert (tmp_path / TRACKER_FILE_NAME).read_text(encoding="utf-8") == before
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_the_tracker_is_readable_like_the_other_extract_files(tmp_path: Path) -> None:
    resolve_run(_db(tmp_path, "run_a.db"))

    assert (tmp_path / TRACKER_FILE_NAME).stat().st_mode & 0o777 == 0o644


def test_a_corrupt_tracker_fails_loudly_instead_of_starting_an_initial_run(tmp_path: Path) -> None:
    (tmp_path / TRACKER_FILE_NAME).write_text("{not json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="delete it to force an initial extract"):
        resolve_run(_db(tmp_path, "run_a.db"))


def test_recording_fails_when_the_tracker_disappeared_mid_run(tmp_path: Path) -> None:
    db_file = _db(tmp_path, "run_a.db")
    run = resolve_run(db_file)
    (tmp_path / TRACKER_FILE_NAME).unlink()

    with pytest.raises(RuntimeError, match="removed during run run_a.db"):
        record_inventory_step(db_file, run, "ambari_hosts")


def test_an_overlapping_run_that_replaced_the_tracker_is_not_credited_with_the_other_runs_step(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    run_a = resolve_run(_db(tmp_path, "run_a.db"))
    resolve_run(_db(tmp_path, "run_b.db"))  # run B starts while run A's step is still fetching

    record_inventory_step(_db(tmp_path, "run_a.db"), run_a, "ambari_hosts")

    assert _tracker(tmp_path)["initial_run_db"] == "run_b.db"
    assert _tracker(tmp_path)["completed_inventory_steps"] == []
    # Run B completes with its own steps only, so a later run still collects ambari_hosts.
    _complete_initial(tmp_path, "run_b.db")
    later = _run(tmp_path, "run_c.db", "ambari_hosts", "ambari", _ambari_hosts(calls))

    assert later.startswith("ambari_hosts extracted")
    assert calls == ["ambari_hosts"]


def test_an_overlapping_run_cannot_mark_its_yarn_cluster_info_in_the_other_runs_tracker(tmp_path: Path) -> None:
    run_a = resolve_run(_db(tmp_path, "run_a.db"))
    resolve_run(_db(tmp_path, "run_b.db"))

    record_initial_gate(_db(tmp_path, "run_a.db"), run_a)

    assert _tracker(tmp_path)["yarn_cluster_info_run_db"] is None


def test_an_incremental_run_records_nothing_into_a_new_initial_extract(tmp_path: Path) -> None:
    _complete_initial(tmp_path, "run_a.db")
    incremental = resolve_run(_db(tmp_path, "run_b.db"))
    (tmp_path / TRACKER_FILE_NAME).unlink()
    resolve_run(_db(tmp_path, "run_c.db"))  # a new initial extract starts while run B is still running

    record_inventory_step(_db(tmp_path, "run_b.db"), incremental, "ambari_hosts")

    assert _tracker(tmp_path)["initial_run_db"] == "run_c.db"
    assert _tracker(tmp_path)["completed_inventory_steps"] == []


# ---- the tracker lock: a scheduled run and a manual one in the same folder ------------------------------------------


def _python(code: str, *args: str) -> subprocess.Popen[str]:
    """Another profiler process, as a second run in the same folder would be."""
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(code), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


_RECORD_STEPS = """
    import sys
    from databricks.labs.lakebridge.resources.assessments.hadoop.extract_runs import ExtractRun, record_inventory_step

    db_path, prefix = sys.argv[1], sys.argv[2]
    run = ExtractRun(run_db=prefix + ".db", initial=False, tracker_owner="run_a.db")
    for i in range(40):
        record_inventory_step(db_path, run, f"{prefix}_{i:02d}")
"""

_HOLD_LOCK = """
    import sys, time
    from pathlib import Path
    from databricks.labs.lakebridge.resources.assessments.hadoop import extract_runs

    with extract_runs._tracker_lock(Path(sys.argv[1])):
        print("locked", flush=True)
        time.sleep(60)
"""


def test_overlapping_runs_lose_none_of_each_others_recorded_steps(tmp_path: Path) -> None:
    _complete_initial(tmp_path, "run_a.db")

    runs = [_python(_RECORD_STEPS, _db(tmp_path, f"{name}.db"), name) for name in ("run_b", "run_c")]
    for run in runs:
        _, err = run.communicate(timeout=25)
        assert run.returncode == 0, err

    expected = sorted(f"{name}_{i:02d}" for name in ("run_b", "run_c") for i in range(40))
    assert _tracker(tmp_path)["completed_inventory_steps"] == expected


def test_a_run_waits_for_the_lock_and_fails_clearly_if_it_is_never_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    holder = _python(_HOLD_LOCK, str(tmp_path / TRACKER_FILE_NAME))
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        monkeypatch.setattr(extract_runs, "LOCK_TIMEOUT_SECONDS", 0.3)

        with pytest.raises(RuntimeError, match="locked by another profiler run"):
            resolve_run(_db(tmp_path, "run_a.db"))
    finally:
        holder.kill()
        holder.communicate(timeout=10)

    # The system released the dead run's lock, so the next run goes ahead.
    assert resolve_run(_db(tmp_path, "run_a.db")).initial is True
    assert (tmp_path / LOCK_FILE_NAME).exists()


# ---- completing the initial extract (the original's check of the active ResourceManager) ----------------------------


def test_an_initial_run_that_aborts_before_its_end_is_initial_again(tmp_path: Path) -> None:
    """The original writes its tracker only at the end of an initial run, so an aborted run leaves none behind."""
    calls: list[str] = []
    _run(tmp_path, "run_a.db", "ambari_hosts", "ambari", _ambari_hosts(calls))
    _yarn_down(tmp_path, "run_a.db")  # the run aborts here: no final step runs

    assert _tracker(tmp_path)["initial_completed"] is False
    again = resolve_run(_db(tmp_path, "run_b.db"))

    assert again.initial is True
    assert again.completed == frozenset()
    assert _tracker(tmp_path)["completed_inventory_steps"] == []


def test_an_initial_run_whose_yarn_cluster_info_failed_is_not_recorded(tmp_path: Path) -> None:
    resolve_run(_db(tmp_path, "run_a.db"))
    _yarn_down(tmp_path, "run_a.db")

    message = finish_run(_db(tmp_path, "run_a.db"))

    assert message == (
        "initial extract not recorded: yarn_cluster_info did not succeed in this run, "
        "or another run took over the extract tracker after it did"
    )
    assert resolve_run(_db(tmp_path, "run_b.db")).initial is True


def test_a_successful_yarn_cluster_info_then_the_final_step_completes_the_run(tmp_path: Path) -> None:
    _yarn_ok(tmp_path, "run_a.db")

    assert finish_run(_db(tmp_path, "run_a.db")) == "initial extract recorded"
    assert _tracker(tmp_path)["initial_completed"] is True
    assert _tracker(tmp_path)["yarn_cluster_info_run_db"] == "run_a.db"


def test_the_final_step_of_an_incremental_run_records_nothing(tmp_path: Path) -> None:
    _complete_initial(tmp_path, "run_a.db")
    before = (tmp_path / TRACKER_FILE_NAME).read_text(encoding="utf-8")

    assert resolve_run(_db(tmp_path, "run_b.db")).initial is False
    message = finish_run(_db(tmp_path, "run_b.db"))

    assert message == "nothing to record: the initial extract was already recorded by run_a.db"
    assert (tmp_path / TRACKER_FILE_NAME).read_text(encoding="utf-8") == before


def test_the_final_step_of_an_initial_run_whose_tracker_was_taken_over_names_the_other_run(tmp_path: Path) -> None:
    _yarn_ok(tmp_path, "run_a.db")
    resolve_run(_db(tmp_path, "run_b.db"))  # run B starts before run A's final step
    before = (tmp_path / TRACKER_FILE_NAME).read_text(encoding="utf-8")

    message = finish_run(_db(tmp_path, "run_a.db"))

    assert message == "initial extract not recorded: another run (run_b.db) took over the extract tracker"
    assert (tmp_path / TRACKER_FILE_NAME).read_text(encoding="utf-8") == before


def test_the_final_step_of_a_run_that_took_its_tracker_back_does_not_record_it(tmp_path: Path) -> None:
    _yarn_ok(tmp_path, "run_a.db")
    resolve_run(_db(tmp_path, "run_b.db"))
    resolve_run(_db(tmp_path, "run_a.db"))  # run A's next step takes the tracker back, afresh

    message = finish_run(_db(tmp_path, "run_a.db"))

    assert message.startswith("initial extract not recorded: yarn_cluster_info did not succeed in this run, or another")
    assert _tracker(tmp_path)["initial_completed"] is False


def test_the_final_step_writes_no_table_and_needs_no_database(tmp_path: Path) -> None:
    _yarn_ok(tmp_path, "run_a.db")
    (tmp_path / "run_a.db").unlink()

    assert finish_run(_db(tmp_path, "run_a.db")) == "initial extract recorded"
    assert not (tmp_path / "run_a.db").exists()


def test_an_inventory_step_that_failed_in_the_initial_run_is_fetched_again_after_completion(tmp_path: Path) -> None:
    def broken(*_: Any) -> list[str]:
        raise RuntimeError("Ambari is down")

    with pytest.raises(RuntimeError, match="Ambari is down"):
        _run(tmp_path, "run_a.db", "ambari_hosts", "ambari", broken)
    _yarn_ok(tmp_path, "run_a.db")
    assert finish_run(_db(tmp_path, "run_a.db")) == "initial extract recorded"

    calls: list[str] = []
    retried = _run(tmp_path, "run_b.db", "ambari_hosts", "ambari", _ambari_hosts(calls))

    assert retried.startswith("ambari_hosts extracted")
    assert calls == ["ambari_hosts"]
    assert _tracker(tmp_path)["completed_inventory_steps"] == ["ambari_hosts"]


# ---- the runner: skipping inventory on incremental runs ------------------------------------------------------------


def test_inventory_is_collected_by_the_initial_run_and_skipped_after_completion(tmp_path: Path) -> None:
    calls: list[str] = []
    first = _run(tmp_path, "run_a.db", "ambari_hosts", "ambari", _ambari_hosts(calls))
    _yarn_ok(tmp_path, "run_a.db")
    assert finish_run(_db(tmp_path, "run_a.db")) == "initial extract recorded"

    second = extract.run_step(
        step="ambari_hosts",
        endpoint="ambari",
        fetch=lambda *_: pytest.fail("an incremental run must not fetch the inventory"),
        sql_dir=_HADOOP,
        db_path=_db(tmp_path, "later.db"),
        creds_file=_creds(tmp_path),
    )

    assert first.startswith("ambari_hosts extracted")
    assert second == SKIPPED_INCREMENTAL
    assert calls == ["ambari_hosts"]
    assert not (tmp_path / "later.db").exists()


def test_a_step_that_is_not_configured_is_not_recorded_as_collected(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.yml"
    creds.write_text(yaml.safe_dump({"hadoop": {"distribution": "hdp", "spark_history": {"url": "http://s:1"}}}))
    message = extract.run_step(
        step="ambari_hosts",
        endpoint="ambari",
        fetch=lambda *_: pytest.fail("not configured, so nothing is fetched"),
        sql_dir=_HADOOP,
        db_path=_db(tmp_path, "run_a.db"),
        creds_file=creds,
    )

    assert message == "skipped: not configured (hadoop.ambari is absent)"
    assert _tracker(tmp_path)["completed_inventory_steps"] == []


def test_every_run_step_is_fetched_on_an_incremental_run(tmp_path: Path) -> None:
    _complete_initial(tmp_path, "run_a.db")
    seen: list[ExtractRun] = []

    def spark(_client: RestClient, _config: Any, run: ExtractRun) -> list[str]:
        seen.append(run)
        return [_fixture("spark_applications_vanilla.json")]

    message = _run(tmp_path, "later.db", "spark_applications", "spark_history", spark)

    assert message.startswith("spark_applications extracted")
    assert [run.initial for run in seen] == [False]


def test_each_run_sees_the_kind_the_tracker_gives_it(tmp_path: Path) -> None:
    kinds: list[bool] = []

    def spark(_client: RestClient, _config: Any, run: ExtractRun) -> list[str]:
        kinds.append(run.initial)
        return ["[]"]

    _run(tmp_path, "run_a.db", "spark_applications", "spark_history", spark)
    _yarn_ok(tmp_path, "run_a.db")
    finish_run(_db(tmp_path, "run_a.db"))
    _run(tmp_path, "run_b.db", "spark_applications", "spark_history", spark)

    assert kinds == [True, False]


# ---- classification ------------------------------------------------------------------------------------------------


def test_every_pipeline_step_is_classified_exactly_once() -> None:
    steps = [step["name"] for step in yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))["steps"]]

    assert len(steps) == len(set(steps))
    assert set(steps) == set(STEP_KINDS) | {RECORD_STEP}
    assert RECORD_STEP not in STEP_KINDS


def test_the_final_step_is_last_and_is_not_optional() -> None:
    steps = PipelineClass.load_config_from_yaml(_PIPELINE).steps

    assert steps[-1].name == RECORD_STEP
    assert steps[-1].optional is False


def test_the_run_every_time_steps_are_the_workload_and_the_rest_are_inventory() -> None:
    every_run = sorted(step for step, kind in STEP_KINDS.items() if kind is StepKind.EVERY_RUN)

    assert every_run == [
        "cm_clusters",
        "cm_impala_queries",
        "cm_timeseries",
        "impala_queries",
        "spark_application_attempts",
        "spark_applications",
        "yarn_applications",
        "yarn_cluster_info",
    ]
    assert all(kind is StepKind.INITIAL_ONLY for step, kind in STEP_KINDS.items() if step not in every_run)


# ---- windows -------------------------------------------------------------------------------------------------------


def _recording() -> tuple[list[tuple[str, dict]], Any]:
    calls: list[tuple[str, dict]] = []

    class _Recording:
        base_url = "http://x"

        def get_text(self, path: str, params: dict | None = None) -> str:
            calls.append((path, dict(params or {})))
            return '{"items": []}'

    return calls, _Recording()


def test_spark_asks_for_a_week_on_the_initial_run_and_yesterday_on_an_incremental_run(fixed_clock: None) -> None:
    config = parse_hadoop_config({"distribution": "hdp", "spark_history": {"url": "http://s:18081"}})
    calls, client = _recording()

    endpoints.spark_applications(client, config, ExtractRun(run_db="a.db_file", initial=True))  # type: ignore[arg-type]
    endpoints.spark_applications(client, config, ExtractRun(run_db="b.db_file", initial=False))  # type: ignore[arg-type]

    assert [params["minDate"] for _, params in calls] == ["2026-11-17", "2026-11-23"]


def test_yarn_applications_have_no_time_window_on_any_run(fixed_clock: None) -> None:
    config = parse_hadoop_config({"distribution": "hdp", "yarn": {"urls": ["http://rm:8088"]}})
    calls, client = _recording()

    endpoints.yarn_applications(client, config, ExtractRun(run_db="b.db_file", initial=False))  # type: ignore[arg-type]

    assert calls == [("/ws/v1/cluster/apps", {})]


def _impala_config() -> Any:
    return parse_hadoop_config(
        {
            "distribution": "cdh",
            "cloudera_manager": {
                "url": "http://cm:7180",
                "username": "u",
                "password": "p",
                "cluster": "c1",
                "impala_service": "impala",
                "extract_impala_queries": True,
                "impala_lookback_days": 1,
            },
        }
    )


class _ImpalaCm:
    base_url = "http://cm:7180"

    def __init__(self) -> None:
        self.windows: set[tuple[str, str]] = set()

    def get_plain_text(self, path: str, params: dict | None = None) -> str:
        return self.get_text(path, params)

    def get_json(self, path: str, params: dict | None = None) -> Any:
        return json.loads(self.get_text(path, params))

    def get_text(self, path: str, params: dict | None = None) -> str:
        if path.endswith("/impalaQueries"):
            assert params is not None
            self.windows.add((params["from"], params["to"]))
            return '{"queries": []}'
        if path == "/api/version":
            return "v12"
        if path == "/api/v12/clusters":
            return json.dumps({"items": [{"name": "c1", "displayName": "c1"}]})
        if path == "/api/v12/clusters/c1/services":
            return json.dumps({"items": [{"name": "impala", "type": "IMPALA"}]})
        raise AssertionError(f"unexpected {path}")


def test_initial_impala_reads_today_and_the_previous_lookback_days(fixed_clock: None) -> None:
    fake = _ImpalaCm()

    list(
        endpoints.cm_impala_queries(
            cast(RestClient, fake), _impala_config(), ExtractRun(run_db="a.db_file", initial=True)
        )
    )

    assert min(fake.windows)[0] == "2026-11-23T00:00:00.000Z"
    assert max(fake.windows)[1] == "2026-11-24T02:29:59.999Z"


def test_incremental_impala_reads_yesterday_only(fixed_clock: None) -> None:
    fake = _ImpalaCm()

    list(
        endpoints.cm_impala_queries(
            cast(RestClient, fake), _impala_config(), ExtractRun(run_db="b.db_file", initial=False)
        )
    )

    assert len(fake.windows) == 24
    assert min(fake.windows)[0] == "2026-11-23T00:00:00.000Z"
    assert max(fake.windows)[1] == "2026-11-23T23:59:59.999Z"


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 24), date(2026, 10, 24)),
        (date(2026, 1, 15), date(2025, 12, 15)),
        (date(2026, 3, 31), date(2026, 2, 28)),
        (date(2028, 3, 31), date(2028, 2, 29)),
        (date(2026, 5, 30), date(2026, 4, 30)),
    ],
)
def test_one_month_before_is_the_original_date_minus_one_month(today: date, expected: date) -> None:
    assert endpoints.one_month_before(today) == expected
