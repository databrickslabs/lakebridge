import dataclasses
import re
from dataclasses import dataclass, field

import logging

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
    dependencies: list[str] = field(default_factory=list)
    comment: str | None = None

    def __post_init__(self) -> None:
        """Validate step configuration to prevent SQL injection and configuration errors."""
        self._validate_name()
        self._validate_mode()
        self._validate_type()

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
        valid_types = {'sql', 'ddl', 'python'}
        if self.type not in valid_types:
            raise ValueError(
                f"Invalid type '{self.type}' for step '{self.name}'. "
                f"Valid types are: {', '.join(sorted(valid_types))}"
            )

    def copy(self, /, **changes) -> "Step":
        return dataclasses.replace(self, **changes)


@dataclass(frozen=True)
class PipelineConfig:
    name: str
    version: str
    extract_folder: str
    comment: str | None = None
    steps: list[Step] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Warn if any active non-DDL step precedes the first active DDL step.
        # Inactive steps are excluded: they are skipped at runtime and have no ordering impact.
        active_steps = [s for s in self.steps if s.flag == "active"]
        first_ddl_index = next((i for i, s in enumerate(active_steps) if s.type == "ddl"), None)
        if first_ddl_index is not None and first_ddl_index > 0:
            early_non_ddl = [s.name for s in active_steps[:first_ddl_index] if s.type != "ddl"]
            if early_non_ddl:
                names = ", ".join(early_non_ddl)
                logger.warning(
                    f"The following active steps run before the first DDL step and may fail if the "
                    f"target tables have not yet been created: {names}. "
                    f"Consider moving DDL steps earlier in the pipeline configuration."
                )

    def copy(self, /, **changes) -> "PipelineConfig":
        return dataclasses.replace(self, **changes)
