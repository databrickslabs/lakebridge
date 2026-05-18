"""Unit tests for the source-agnostic preflight framework."""

from __future__ import annotations

import pytest

from databricks.labs.lakebridge.connections.preflight import (
    CheckResult,
    CheckSeverity,
    CheckStatus,
    PreflightCheck,
    PreflightReport,
    PreflightRunner,
    RunOptions,
)


class _StaticCheck(PreflightCheck):
    """Test double whose ``run`` returns a preconfigured result.

    Lets us assert runner semantics (ordering, short-circuit, fail-fast)
    without depending on real probes.
    """

    def __init__(
        self,
        name: str,
        status: CheckStatus,
        severity: CheckSeverity = CheckSeverity.FATAL,
        depends_on: list[str] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.name = name
        self.status = status
        self.severity = severity
        self.depends_on = depends_on or []
        self.parallel_safe = True
        self._raises = raises

    def run(self, context, options):
        if self._raises is not None:
            raise self._raises
        return CheckResult(name=self.name, severity=self.severity, status=self.status, detail="ok")


def _factory(*checks: PreflightCheck):
    def _f(_context):
        return list(checks)

    return _f


def test_run_options_defaults_fast():
    opts = RunOptions()
    assert opts.thorough is False
    assert opts.fail_fast is False
    assert opts.connect_timeout_s == 10
    assert opts.max_workers == 8
    assert opts.serverless_db_sample_size == 10
    assert opts.short_circuit_dependencies is True


def test_run_options_thorough_overrides():
    opts = RunOptions(thorough=True)
    assert opts.connect_timeout_s == 30
    assert opts.serverless_db_sample_size is None
    assert opts.short_circuit_dependencies is False


def test_run_options_thorough_and_fail_fast_compose():
    opts = RunOptions(thorough=True, fail_fast=True)
    assert opts.fail_fast is True
    assert opts.connect_timeout_s == 30


def test_runner_registers_and_runs_all_checks():
    runner = PreflightRunner()
    runner.register(
        "demo",
        _factory(
            _StaticCheck("a", CheckStatus.PASS),
            _StaticCheck("b", CheckStatus.PASS),
        ),
    )
    report = runner.run("demo", {})
    assert [r.name for r in report.results] == ["a", "b"]
    assert all(r.status == CheckStatus.PASS for r in report.results)
    assert not report.any_fatal_failed()


def test_runner_raises_for_unknown_source():
    runner = PreflightRunner()
    with pytest.raises(ValueError, match="No preflight checks registered"):
        runner.run("nope", {})


def test_runner_short_circuits_on_failed_dependency():
    runner = PreflightRunner()
    runner.register(
        "demo",
        _factory(
            _StaticCheck("upstream", CheckStatus.FAIL),
            _StaticCheck("downstream", CheckStatus.PASS, depends_on=["upstream"]),
        ),
    )
    report = runner.run("demo", {})
    assert [r.name for r in report.results] == ["upstream", "downstream"]
    assert report.results[1].status == CheckStatus.SKIP
    assert "upstream" in report.results[1].detail


def test_runner_propagates_skip_through_chain():
    runner = PreflightRunner()
    runner.register(
        "demo",
        _factory(
            _StaticCheck("root", CheckStatus.FAIL),
            _StaticCheck("mid", CheckStatus.PASS, depends_on=["root"]),
            _StaticCheck("leaf", CheckStatus.PASS, depends_on=["mid"]),
        ),
    )
    report = runner.run("demo", {})
    statuses = {r.name: r.status for r in report.results}
    assert statuses == {"root": CheckStatus.FAIL, "mid": CheckStatus.SKIP, "leaf": CheckStatus.SKIP}


def test_runner_disables_short_circuit_in_thorough_mode():
    runner = PreflightRunner()
    runner.register(
        "demo",
        _factory(
            _StaticCheck("upstream", CheckStatus.FAIL),
            _StaticCheck("downstream", CheckStatus.PASS, depends_on=["upstream"]),
        ),
    )
    report = runner.run("demo", {}, RunOptions(thorough=True))
    assert report.results[1].status == CheckStatus.PASS, "thorough mode runs the dependent anyway"


def test_runner_fail_fast_stops_after_first_fatal():
    runner = PreflightRunner()
    runner.register(
        "demo",
        _factory(
            _StaticCheck("a", CheckStatus.FAIL),
            _StaticCheck("b", CheckStatus.PASS),
            _StaticCheck("c", CheckStatus.PASS),
        ),
    )
    report = runner.run("demo", {}, RunOptions(fail_fast=True))
    statuses = {r.name: r.status for r in report.results}
    assert statuses["a"] == CheckStatus.FAIL
    assert statuses["b"] == CheckStatus.SKIP
    assert statuses["c"] == CheckStatus.SKIP


def test_runner_fail_fast_only_triggers_on_fatal():
    runner = PreflightRunner()
    runner.register(
        "demo",
        _factory(
            _StaticCheck("a", CheckStatus.FAIL, severity=CheckSeverity.WARN),
            _StaticCheck("b", CheckStatus.PASS),
        ),
    )
    report = runner.run("demo", {}, RunOptions(fail_fast=True))
    statuses = {r.name: r.status for r in report.results}
    assert statuses == {"a": CheckStatus.FAIL, "b": CheckStatus.PASS}, "WARN FAILs do not trigger fail_fast"


def test_runner_converts_exceptions_into_fail_results():
    runner = PreflightRunner()
    runner.register(
        "demo",
        _factory(_StaticCheck("boom", CheckStatus.PASS, raises=RuntimeError("kaboom"))),
    )
    report = runner.run("demo", {})
    assert report.results[0].status == CheckStatus.FAIL
    assert "kaboom" in report.results[0].detail


def test_runner_sets_elapsed_ms_on_results():
    runner = PreflightRunner()
    runner.register("demo", _factory(_StaticCheck("x", CheckStatus.PASS)))
    report = runner.run("demo", {})
    assert report.results[0].elapsed_ms >= 0


def test_any_fatal_failed_semantics():
    report = PreflightReport(
        results=[
            CheckResult(name="a", severity=CheckSeverity.WARN, status=CheckStatus.FAIL),
            CheckResult(name="b", severity=CheckSeverity.FATAL, status=CheckStatus.PASS),
        ]
    )
    assert not report.any_fatal_failed()
    report.add(CheckResult(name="c", severity=CheckSeverity.FATAL, status=CheckStatus.FAIL))
    assert report.any_fatal_failed()


def test_to_text_renders_pass_fail_remediation():
    report = PreflightReport()
    report.add(CheckResult(name="ok", severity=CheckSeverity.FATAL, status=CheckStatus.PASS, detail="all good"))
    report.add(
        CheckResult(
            name="bad",
            severity=CheckSeverity.FATAL,
            status=CheckStatus.FAIL,
            detail="boom",
            remediation="fix it",
        )
    )
    text = report.to_text()
    assert "ok" in text and "all good" in text
    assert "bad" in text and "boom" in text
    assert "-> fix it" in text


def test_to_dict_is_serializable():
    report = PreflightReport()
    report.add(CheckResult(name="x", severity=CheckSeverity.INFO, status=CheckStatus.PASS, detail="d", elapsed_ms=42))
    d = report.to_dict()
    assert d == [
        {
            "name": "x",
            "severity": CheckSeverity.INFO,
            "status": CheckStatus.PASS,
            "detail": "d",
            "remediation": "",
            "elapsed_ms": 42,
        }
    ]


def test_is_registered_lookup():
    runner = PreflightRunner()
    assert not runner.is_registered("synapse")
    runner.register("Synapse", _factory(_StaticCheck("c", CheckStatus.PASS)))
    assert runner.is_registered("synapse")
    assert runner.is_registered("SYNAPSE")
