from dataclasses import dataclass, field


@dataclass
class Step:
    name: str
    type: str | None
    extract_source: str
    mode: str = "append"
    frequency: str = "once"
    flag: str = "active"
    dependencies: list[str] = field(default_factory=list)
    comment: str | None = None


@dataclass
class PipelineConfig:
    name: str
    version: str
    extract_folder: str
    comment: str | None = None
    steps: list[Step] = field(default_factory=list)
