from dataclasses import dataclass, field
from typing import Any


@dataclass
class TableFQN:
    catalog: str | None
    schema: str
    name: str

    @property
    def fqn(self) -> str:
        if self.catalog:
            return f"{self.catalog}.{self.schema}.{self.name}"
        return f"{self.schema}.{self.name}"


@dataclass
class FieldInfo:
    name: str
    data_type: str
    nullable: bool | None = None
    metadata: dict[str, Any] | None = None
    comment: str | None = None


@dataclass
class TableDefinition:
    fqn: TableFQN
    location: str | None = None
    table_format: str | None = None
    view_text: str | None = None
    columns: list[FieldInfo] = field(default_factory=list)
    primary_keys: list[str] | None = None
    size_gb: int | None = None
    comment: str | None = None
