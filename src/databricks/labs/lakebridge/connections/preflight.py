"""Source-agnostic preflight framework for the database profiler.

Provides reusable abstractions (:class:`PreflightCheck`, :class:`PreflightReport`,
:class:`PreflightRunner`) that source-specific check suites plug into via a
registry keyed by ``source_tech``. The runner handles thread-pool execution,
dependency short-circuit, and ``fail_fast`` semantics so individual checks stay
small and declarative.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CheckSeverity(str, Enum):
    """How a failed check should be treated.

    FATAL: any FAIL on this check should block the profiler run.
    WARN:  a FAIL is suspicious but does not block.
    INFO:  observational only.
    """

    FATAL = "FATAL"
    WARN = "WARN"
    INFO = "INFO"


class CheckStatus(str, Enum):
    """Final outcome of a check after the runner executes it."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    UNKNOWN = "UNKNOWN"


@dataclasses.dataclass
class CheckResult:
    """Outcome of a single :class:`PreflightCheck`.

    ``remediation`` is meant to be a one-line, actionable hint that gets printed
    alongside the failure so the user has a next step without leaving the
    terminal.
    """

    name: str
    severity: CheckSeverity
    status: CheckStatus
    detail: str = ""
    remediation: str = ""
    elapsed_ms: int = 0


@dataclasses.dataclass
class RunOptions:
    """User-tunable knobs for a preflight run.

    The defaults target a fast configure-time run (~30-60 s on a healthy
    workspace, capped under ~90 s when serverless DBs are broken). Passing
    ``thorough=True`` flips the timing/sampling defaults to a deep sweep
    suitable for investigation runs.
    """

    thorough: bool = False
    fail_fast: bool = False
    connect_timeout_s: int = 10
    max_workers: int = 8
    serverless_db_sample_size: int | None = 10
    short_circuit_dependencies: bool = True

    def __post_init__(self) -> None:
        if self.thorough:
            self.connect_timeout_s = 30
            self.serverless_db_sample_size = None
            self.short_circuit_dependencies = False


@dataclasses.dataclass
class PreflightReport:
    """Aggregated results from a runner invocation.

    Renders as a readable text table for the CLI and as a plain dict for
    machine-readable consumers (the dashboard ingest job, telemetry, tests).
    """

    results: list[CheckResult] = dataclasses.field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    def any_fatal_failed(self) -> bool:
        return any(r.severity == CheckSeverity.FATAL and r.status == CheckStatus.FAIL for r in self.results)

    def to_dict(self) -> list[dict[str, Any]]:
        return [dataclasses.asdict(r) for r in self.results]

    def to_text(self) -> str:
        if not self.results:
            return "(no preflight checks ran)"
        name_w = max(len("Check"), max(len(r.name) for r in self.results))
        sev_w = max(len("Severity"), max(len(r.severity.value) for r in self.results))
        status_w = max(len("Status"), max(len(r.status.value) for r in self.results))
        time_w = max(len("Time"), max(len(f"{r.elapsed_ms}ms") for r in self.results))

        header = f"{'Check':<{name_w}}  {'Severity':<{sev_w}}  {'Status':<{status_w}}  {'Time':<{time_w}}  Detail"
        rule = "-" * len(header)
        lines = [header, rule]
        for r in self.results:
            lines.append(
                f"{r.name:<{name_w}}  "
                f"{r.severity.value:<{sev_w}}  "
                f"{r.status.value:<{status_w}}  "
                f"{r.elapsed_ms}ms".ljust(name_w + sev_w + status_w + time_w + 8)
                + f"  {r.detail}"
            )
            if r.remediation and r.status in (CheckStatus.FAIL, CheckStatus.UNKNOWN):
                lines.append(f"{'':<{name_w}}  {'':<{sev_w}}  {'':<{status_w}}  {'':<{time_w}}  -> {r.remediation}")
        return "\n".join(lines)


class PreflightCheck(ABC):
    """A single, source-specific preflight probe.

    Subclasses must:

    - set :attr:`name` (unique within a source suite, used by ``depends_on``),
    - set :attr:`severity` (controls whether a FAIL blocks the profiler run),
    - optionally declare :attr:`depends_on` to enable dependency short-circuit,
    - optionally set :attr:`parallel_safe` if the check can be run on a worker
      thread alongside other parallel-safe checks.

    ``run`` returns a :class:`CheckResult`. Raising is also fine; the runner
    catches and converts to a ``FAIL`` result.
    """

    name: str = ""
    severity: CheckSeverity = CheckSeverity.FATAL
    depends_on: list[str] = []
    parallel_safe: bool = False

    @abstractmethod
    def run(self, context: dict[str, Any], options: RunOptions) -> CheckResult:
        """Execute the check and return its result."""

    def _result(
        self,
        status: CheckStatus,
        *,
        detail: str = "",
        remediation: str = "",
        elapsed_ms: int = 0,
    ) -> CheckResult:
        return CheckResult(
            name=self.name,
            severity=self.severity,
            status=status,
            detail=detail,
            remediation=remediation,
            elapsed_ms=elapsed_ms,
        )


class PreflightRunner:
    """Source-tech registry + executor for preflight check suites.

    Register a callable that yields the list of checks for a given source_tech
    (lazy so we don't import optional dependencies at import time), then call
    :meth:`run` to execute them.
    """

    def __init__(self) -> None:
        self._registry: dict[str, Callable[[dict[str, Any]], list[PreflightCheck]]] = {}

    def register(self, source_tech: str, factory: Callable[[dict[str, Any]], list[PreflightCheck]]) -> None:
        self._registry[source_tech.lower()] = factory

    def is_registered(self, source_tech: str) -> bool:
        return source_tech.lower() in self._registry

    def run(self, source_tech: str, raw_config: dict[str, Any], options: RunOptions | None = None) -> PreflightReport:
        options = options or RunOptions()
        if source_tech.lower() not in self._registry:
            raise ValueError(f"No preflight checks registered for source_tech={source_tech!r}")

        context: dict[str, Any] = {"raw_config": raw_config, "shared": {}}
        checks = self._registry[source_tech.lower()](context)
        report = PreflightReport()

        # Track results per check name so dependency lookups can find them.
        by_name: dict[str, CheckResult] = {}

        with ThreadPoolExecutor(max_workers=options.max_workers) as pool:
            context["pool"] = pool
            for check in checks:
                if options.fail_fast and report.any_fatal_failed():
                    skipped = CheckResult(
                        name=check.name,
                        severity=check.severity,
                        status=CheckStatus.SKIP,
                        detail="Skipped due to --fail-fast and an earlier FATAL failure.",
                    )
                    by_name[check.name] = skipped
                    report.add(skipped)
                    continue

                skipped_for_dep = self._dependency_skip(check, by_name, options)
                if skipped_for_dep is not None:
                    by_name[check.name] = skipped_for_dep
                    report.add(skipped_for_dep)
                    continue

                result = self._execute(check, context, options)
                by_name[check.name] = result
                report.add(result)

        return report

    @staticmethod
    def _dependency_skip(
        check: PreflightCheck,
        by_name: dict[str, CheckResult],
        options: RunOptions,
    ) -> CheckResult | None:
        """If short-circuit is enabled and any declared dependency FAILed, skip."""
        if not options.short_circuit_dependencies or not check.depends_on:
            return None
        for dep in check.depends_on:
            dep_result = by_name.get(dep)
            if dep_result is None:
                continue
            if dep_result.status == CheckStatus.FAIL:
                return CheckResult(
                    name=check.name,
                    severity=check.severity,
                    status=CheckStatus.SKIP,
                    detail=f"Skipped because dependency {dep!r} failed.",
                )
            if dep_result.status == CheckStatus.SKIP:
                return CheckResult(
                    name=check.name,
                    severity=check.severity,
                    status=CheckStatus.SKIP,
                    detail=f"Skipped because dependency {dep!r} was skipped.",
                )
        return None

    @staticmethod
    def _execute(check: PreflightCheck, context: dict[str, Any], options: RunOptions) -> CheckResult:
        start = time.monotonic()
        try:
            result = check.run(context, options)
        except Exception as e:  # noqa: BLE001
            elapsed = int((time.monotonic() - start) * 1000)
            logger.debug(f"Preflight check {check.name!r} raised: {e!r}", exc_info=True)
            return CheckResult(
                name=check.name,
                severity=check.severity,
                status=CheckStatus.FAIL,
                detail=f"Unexpected error: {e}",
                elapsed_ms=elapsed,
            )

        if result.elapsed_ms == 0:
            result.elapsed_ms = int((time.monotonic() - start) * 1000)
        return result


# Module-level singleton; source suites register themselves at import time.
runner = PreflightRunner()
