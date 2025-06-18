from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


# not using StrEnum because they only appear with Python 3.11
class ErrorSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ErrorKind(Enum):
    ANALYSIS = "ANALYSIS"
    PARSING = "PARSING"
    GENERATION = "GENERATION"
    VALIDATION = "VALIDATION"
    INTERNAL = "INTERNAL"


@dataclass
class CodePosition:
    line: int  # 0-based line number
    character: int  # 0-based character number


@dataclass
class CodeRange:
    start: CodePosition
    end: CodePosition


@dataclass
class TranspileError:
    code: str
    kind: ErrorKind
    severity: ErrorSeverity
    path: Path
    message: str
    range: CodeRange | None = None

    def __str__(self):
        return f"{type(self).__name__}(code={self.code}, kind={self.kind.name}, severity={self.severity.name}, path='{self.path!s}', message='{self.message}')"


@dataclass
class TranspileStatus:
    file_list: list[Path]
    no_of_transpiled_queries: int
    error_list: list[TranspileError]

    @property
    def analysis_error_count(self) -> int:
        return len([error for error in self.error_list if error.kind == ErrorKind.ANALYSIS])

    @property
    def parsing_error_count(self) -> int:
        return len([error for error in self.error_list if error.kind == ErrorKind.PARSING])

    @property
    def generation_error_count(self) -> int:
        return len([error for error in self.error_list if error.kind == ErrorKind.GENERATION])

    @property
    def validation_error_count(self) -> int:
        return len([error for error in self.error_list if error.kind == ErrorKind.VALIDATION])
