from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any

from databricks.labs.lakebridge.discovery.table import TableDefinition


class TableDefinitionService(ABC):

    def __init__(self, connection: Any):
        self.connection = connection

    @abstractmethod
    def get_table_definition(self, catalog_name: str) -> Iterable[TableDefinition]:
        pass

    @abstractmethod
    def _get_table_definition_query(self, catalog_name: str) -> str:
        pass

    @abstractmethod
    def get_all_catalog(self) -> Iterable[str]:
        pass
