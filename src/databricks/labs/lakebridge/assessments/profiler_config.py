import dataclasses
from dataclasses import dataclass, field


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

    def copy(self, /, **changes) -> "PipelineConfig":
        return dataclasses.replace(self, **changes)
