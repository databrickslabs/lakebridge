from __future__ import annotations
import abc
from collections.abc import Sequence
from pathlib import Path

from databricks.labs.lakebridge.config import TranspileResult, TranspileConfig


class TranspileEngine(abc.ABC):
    @abc.abstractmethod
    async def initialize(self, config: TranspileConfig) -> None: ...

    @abc.abstractmethod
    async def shutdown(self) -> None: ...

    @abc.abstractmethod
    async def transpile(
        self, source_dialect: str, target_dialect: str, source_code: str, file_path: Path
    ) -> TranspileResult: ...

    @property
    @abc.abstractmethod
    def transpiler_name(self) -> str: ...

    @property
    @abc.abstractmethod
    def supported_dialects(self) -> Sequence[str]: ...

    @abc.abstractmethod
    def is_supported_file(self, file: Path) -> bool: ...
