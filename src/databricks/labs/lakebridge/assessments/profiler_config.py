import dataclasses
import logging
import re
from dataclasses import dataclass, field

# Valid SQL identifier pattern: must start with letter or underscore,
# followed by letters, numbers, or underscores only
_VALID_IDENTIFIER_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Step:
    name: str
    type: str
    extract_source: str
    mode: str = "append"
    frequency: str = "once"
    flag: str = "active"
    comment: str | None = None
    optional: bool = False
    ddl_source: str | None = None

    def __post_init__(self) -> None:
        """Validate step configuration to prevent SQL injection and configuration errors."""
        self._validate_name()
        self._validate_mode()
        self._validate_type()
        self._validate_optional()
        self._validate_ddl_source()

    def _validate_name(self) -> None:
        """Validate step name uses only safe SQL identifier characters."""
        if not self.name:
            raise ValueError("Step name cannot be empty")

        if not _VALID_IDENTIFIER_PATTERN.match(self.name):
            raise ValueError(
                f"Invalid step name: '{self.name}'\n"
                f"Step names must:\n"
                f"  - Start with a letter or underscore\n"
                f"  - Contain only letters, numbers, and underscores\n"
                f"  - Not contain spaces, quotes, semicolons, or special characters\n"
                f"Examples: inventory, user_data, db_extract_01"
            )

        if len(self.name) > 255:
            raise ValueError(
                f"Step name '{self.name}' is too long ({len(self.name)} characters). "
                f"Maximum length is 255 characters."
            )

    def _validate_mode(self) -> None:
        """Validate mode is a recognized value."""
        valid_modes = {'append', 'overwrite'}
        if self.mode not in valid_modes:
            raise ValueError(
                f"Invalid mode '{self.mode}' for step '{self.name}'. "
                f"Valid modes are: {', '.join(sorted(valid_modes))}"
            )

    def _validate_type(self) -> None:
        """Validate type is a recognized value."""
        valid_types = {'sql', 'python', 'source_ddl'}
        if self.type not in valid_types:
            raise ValueError(
                f"Invalid type '{self.type}' for step '{self.name}'. "
                f"Valid types are: {', '.join(sorted(valid_types))}"
            )

    def _validate_optional(self) -> None:
        if not isinstance(self.optional, bool):
            raise ValueError(f"Invalid optional value for step '{self.name}': {self.optional!r}. Expected a boolean.")

    def _validate_ddl_source(self) -> None:
        if self.type == "sql":
            if not self.ddl_source:
                raise ValueError(
                    f"Step '{self.name}' of type 'sql' requires ddl_source "
                    f"(path to a DuckDB CREATE TABLE statement)."
                )
            return
        if self.ddl_source is not None:
            raise ValueError(
                f"Step '{self.name}' of type '{self.type}' must not set ddl_source "
                f"(ddl_source is only valid for type 'sql')."
            )

    def copy(self, /, **changes) -> "Step":
        return dataclasses.replace(self, **changes)


@dataclass(frozen=True)
class PipelineConfig:
    name: str
    version: str
    comment: str | None = None
    steps: list[Step] = field(default_factory=list)

    def copy(self, /, **changes) -> "PipelineConfig":
        return dataclasses.replace(self, **changes)
