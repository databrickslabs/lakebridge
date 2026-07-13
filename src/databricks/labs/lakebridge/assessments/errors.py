from dataclasses import dataclass
from enum import Enum

_ABSENCE_SQLSTATES = frozenset({"42P01", "42703", "42S02", "3F000"})
_PERMISSION_SQLSTATES = frozenset({"42501"})
_SYNTAX_SQLSTATES = frozenset({"42601"})


class ErrorCategory(str, Enum):
    CONNECTION = "CONNECTION"
    AUTH = "AUTH"
    ABSENCE = "ABSENCE"
    PERMISSION = "PERMISSION"
    SYNTAX = "SYNTAX"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SourceFailure:
    category: ErrorCategory
    reason: str
    sqlstate: str | None = None
    vendor_code: str | None = None

    @property
    def is_fatal(self) -> bool:
        return self.category in {ErrorCategory.CONNECTION, ErrorCategory.AUTH}


class SourceQueryError(Exception):
    def __init__(self, failure: SourceFailure, *, step_name: str | None = None) -> None:
        self.failure = failure
        self.step_name = step_name
        super().__init__(failure.reason)

    @property
    def category(self) -> ErrorCategory:
        return self.failure.category

    @property
    def sqlstate(self) -> str | None:
        return self.failure.sqlstate

    @property
    def reason(self) -> str:
        return self.failure.reason

    def is_fatal(self) -> bool:
        return self.failure.is_fatal


def classify_standard_sqlstate(sqlstate: str | None) -> ErrorCategory | None:
    """Map portable SQLSTATE values to a concern category, when recognized."""
    if not sqlstate:
        return None
    if sqlstate.startswith("08"):
        return ErrorCategory.CONNECTION
    if sqlstate.startswith("28"):
        return ErrorCategory.AUTH
    if sqlstate in _ABSENCE_SQLSTATES:
        return ErrorCategory.ABSENCE
    if sqlstate in _PERMISSION_SQLSTATES:
        return ErrorCategory.PERMISSION
    if sqlstate in _SYNTAX_SQLSTATES:
        return ErrorCategory.SYNTAX
    return None
