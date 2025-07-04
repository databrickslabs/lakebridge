import logging
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any, Literal, cast

from databricks.labs.blueprint.installation import JsonValue
from databricks.labs.blueprint.tui import Prompts
from databricks.labs.lakebridge.transpiler.transpile_status import TranspileError
from databricks.labs.lakebridge.reconcile.recon_config import Table


logger = logging.getLogger(__name__)


class LSPPromptMethod(Enum):
    FORCE = auto()  # for mandatory values that are specific to a dialect
    QUESTION = auto()
    CHOICE = auto()
    CONFIRM = auto()


@dataclass
class LSPConfigOptionV1:
    flag: str
    method: LSPPromptMethod
    prompt: str = ""
    choices: list[str] | None = None
    default: Any = None

    @classmethod
    def parse_all(cls, data: dict[str, Any]) -> dict[str, list["LSPConfigOptionV1"]]:
        return {key: list(LSPConfigOptionV1.parse(item) for item in value) for (key, value) in data.items()}

    @classmethod
    def parse(cls, data: Any) -> "LSPConfigOptionV1":
        if not isinstance(data, dict):
            raise ValueError(f"Invalid transpiler config option, expecting a dict entry, got {data}")
        flag: str = data.get("flag", "")
        if not flag:
            raise ValueError(f"Missing 'flag' entry in {data}")
        method_name: str = data.get("method", "")
        if not method_name:
            raise ValueError(f"Missing 'method' entry in {data}")
        method: LSPPromptMethod = cast(LSPPromptMethod, LSPPromptMethod[method_name])
        prompt: str = data.get("prompt", "")
        if not prompt:
            raise ValueError(f"Missing 'prompt' entry in {data}")
        choices = data.get("choices", [])
        default = data.get("default", None)
        return LSPConfigOptionV1(flag, method, prompt, choices, default)

    def prompt_for_value(self, prompts: Prompts) -> JsonValue:
        if self.method == LSPPromptMethod.FORCE:
            return self.default
        if self.method == LSPPromptMethod.CONFIRM:
            return prompts.confirm(self.prompt)
        if self.method == LSPPromptMethod.QUESTION:
            default = self.default if self.default else "None"
            result = prompts.question(self.prompt, default=default)
            if result == "<none>":
                return None
            return result
        if self.method == LSPPromptMethod.CHOICE:
            return prompts.choice(self.prompt, cast(list[str], self.choices))
        raise ValueError(f"Unsupported prompt method: {self.method}")


@dataclass
class TranspileConfig:
    __file__ = "config.yml"
    __version__ = 3

    transpiler_config_path: str | None = None
    source_dialect: str | None = None
    input_source: str | None = None
    output_folder: str | None = None
    error_file_path: str | None = None
    sdk_config: dict[str, str] | None = None
    skip_validation: bool = False
    catalog_name: str = "remorph"
    schema_name: str = "transpiler"
    transpiler_options: JsonValue = None

    @property
    def transpiler_path(self) -> Path | None:
        return Path(self.transpiler_config_path) if self.transpiler_config_path is not None else None

    @property
    def input_path(self) -> Path:
        if self.input_source is None:
            raise ValueError("Missing input source!")
        return Path(self.input_source)

    @property
    def output_path(self) -> Path | None:
        return None if self.output_folder is None else Path(self.output_folder)

    @property
    def error_path(self) -> Path | None:
        return Path(self.error_file_path) if self.error_file_path else None

    @property
    def target_dialect(self) -> Literal["databricks"]:
        return "databricks"

    @classmethod
    def v1_migrate(cls, raw: dict[str, Any]) -> dict[str, Any]:
        raw["version"] = 2
        return raw

    @classmethod
    def v2_migrate(cls, raw: dict[str, Any]) -> dict[str, Any]:
        del raw["mode"]
        key_mapping = {"input_sql": "input_source", "output_folder": "output_path", "source": "source_dialect"}
        raw["version"] = 3
        raw["error_file_path"] = "error_log.txt"
        return {key_mapping.get(key, key): value for key, value in raw.items()}


@dataclass
class TableRecon:
    __file__ = "recon_config.yml"
    __version__ = 1

    source_schema: str
    target_catalog: str
    target_schema: str
    tables: list[Table]
    source_catalog: str | None = None

    def __post_init__(self):
        self.source_schema = self.source_schema.lower()
        self.target_schema = self.target_schema.lower()
        self.target_catalog = self.target_catalog.lower()
        self.source_catalog = self.source_catalog.lower() if self.source_catalog else self.source_catalog


@dataclass
class DatabaseConfig:
    source_schema: str
    target_catalog: str
    target_schema: str
    source_catalog: str | None = None


@dataclass
class TranspileResult:
    transpiled_code: str
    success_count: int
    error_list: list[TranspileError]


@dataclass
class ValidationResult:
    validated_sql: str
    exception_msg: str | None


@dataclass
class ReconcileTablesConfig:
    filter_type: str  # all/include/exclude
    tables_list: list[str]  # [*, table1, table2]


@dataclass
class ReconcileMetadataConfig:
    catalog: str = "remorph"
    schema: str = "reconcile"
    volume: str = "reconcile_volume"


@dataclass
class ReconcileConfig:
    __file__ = "reconcile.yml"
    __version__ = 1

    data_source: str
    report_type: str
    secret_scope: str
    database_config: DatabaseConfig
    metadata_config: ReconcileMetadataConfig
    job_id: str | None = None
    tables: ReconcileTablesConfig | None = None


@dataclass
class RemorphConfigs:
    transpile: TranspileConfig | None = None
    reconcile: ReconcileConfig | None = None
