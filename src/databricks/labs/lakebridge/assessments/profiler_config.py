import dataclasses
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


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

    def copy(self, /, **changes) -> "Step":
        return dataclasses.replace(self, **changes)


@dataclass(frozen=True)
class PipelineConfig:
    name: str
    version: str
    extract_folder: str
    comment: str | None = None
    steps: list[Step] = field(default_factory=list)

    def __post_init__(self):
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
