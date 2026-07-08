import re
from enum import Enum

# Teradata driver embeds numeric codes in messages when SQLSTATE is unavailable.
_TERADATA_ABSENCE_CODES = frozenset({"3807"})
_TERADATA_PERMISSION_CODES = frozenset({"3523"})

_ABSENCE_SQLSTATES = frozenset({"42P01", "42703", "42S02", "3F000"})
_PERMISSION_SQLSTATES = frozenset({"42501"})
_SYNTAX_SQLSTATES = frozenset({"42601"})

_TERADATA_ERROR_CODE_PATTERN = re.compile(r"\[Error (\d+)\]")


class ErrorCategory(str, Enum):
    CONNECTION = "CONNECTION"
    AUTH = "AUTH"
    ABSENCE = "ABSENCE"
    PERMISSION = "PERMISSION"
    SYNTAX = "SYNTAX"
    UNKNOWN = "UNKNOWN"


class SourceQueryError(Exception):
    def __init__(
        self,
        category: ErrorCategory,
        sqlstate: str | None,
        reason: str,
        *,
        step_name: str | None = None,
    ) -> None:
        self.category = category
        self.sqlstate = sqlstate
        self.reason = reason
        self.step_name = step_name
        super().__init__(reason)

    def is_fatal(self) -> bool:
        return self.category in {ErrorCategory.CONNECTION, ErrorCategory.AUTH}


def _classify_teradata_message(message: str) -> ErrorCategory | None:
    match = _TERADATA_ERROR_CODE_PATTERN.search(message)
    if not match:
        return None
    code = match.group(1)
    if code in _TERADATA_ABSENCE_CODES:
        return ErrorCategory.ABSENCE
    if code in _TERADATA_PERMISSION_CODES:
        return ErrorCategory.PERMISSION
    return None


def classify_sqlstate(sqlstate: str | None, message: str = "") -> ErrorCategory:
    if sqlstate:
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

    teradata_category = _classify_teradata_message(message)
    if teradata_category is not None:
        return teradata_category

    return ErrorCategory.UNKNOWN
