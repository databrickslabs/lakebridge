import dataclasses
import re
from dataclasses import dataclass, field

# Valid SQL identifier pattern: must start with letter or underscore,
# followed by letters, numbers, or underscores only
_VALID_IDENTIFIER_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


@dataclass(frozen=True)
class Step:
    name: str
    type: str | None
    extract_source: str
    mode: str = "append"
    frequency: str = "once"
    flag: str = "active"
    dependencies: list[str] = field(default_factory=list)
    comment: str | None = None

    def __post_init__(self):
        """Validate step configuration to prevent SQL injection and configuration errors."""
        self._validate_name()
        self._validate_mode()
        self._validate_type()

    def _validate_name(self):
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

    def _validate_mode(self):
        """Validate mode is a recognized value."""
        valid_modes = {'append', 'overwrite'}
        if self.mode not in valid_modes:
            raise ValueError(
                f"Invalid mode '{self.mode}' for step '{self.name}'. "
                f"Valid modes are: {', '.join(sorted(valid_modes))}"
            )

    def _validate_type(self):
        """Validate type is a recognized value."""
        valid_types = {'sql', 'ddl', 'python', None}
        if self.type not in valid_types:
            valid_types_str = ', '.join(f"'{t}'" if t else 'None' for t in sorted(valid_types, key=lambda x: (x is None, x)))
            raise ValueError(
                f"Invalid type '{self.type}' for step '{self.name}'. "
                f"Valid types are: {valid_types_str}"
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

    def copy(self, /, **changes) -> "PipelineConfig":
        return dataclasses.replace(self, **changes)
