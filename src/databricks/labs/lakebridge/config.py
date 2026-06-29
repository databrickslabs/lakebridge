import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

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


E = TypeVar("E", bound=Enum)


def extract_string_field(data: Mapping[str, JsonValue], name: str) -> str:
    """Extract a string field from the given mapping.

    Parameters:
        data: The mapping to get the string field from.
        name: The name of the field to extract.
    Raises:
        ValueError: If the field is not present, not a string, or an empty string.
    """
    value = _maybe_extract_string_field(data, name, is_required=True)
    if not value:
        msg = f"Invalid '{name}' attribute, must be a non-empty string: {value}"
        raise ValueError(msg)
    return value


def _maybe_extract_string_field(data: Mapping[str, JsonValue], name: str, *, is_required: bool) -> str | None:
    # A variant of extract_string_field() with two differences:
    #  - It allows for optional fields.
    #  - A provided string may be empty.
    # (This can't easily be folded into extract_string_field() because of the different return type.)
    try:
        value = data[name]
    except KeyError as e:
        if is_required:
            raise ValueError(f"Missing '{name}' attribute in {data}") from e
        return None
    if not isinstance(value, str):
        msg = f"Invalid '{name}' entry in {data}, expecting a string: {value}"
        raise ValueError(msg)
    return value


def extract_enum_field(data: Mapping[str, JsonValue], name: str, enum_type: type[E]) -> E:
    """Extract an enum field from the given mapping.

    Parameters:
        data: The mapping to get the enum field from.
        name: The name of the field to extract.
        enum_type: The enum type to use for parsing the value.
    Raises:
        ValueError: If the field is not present and no default is provided, or if it's present but not a valid enum value.
    """
    enum_value = extract_string_field(data, name)
    try:
        return enum_type[enum_value]
    except ValueError as e:
        valid_values = [m.name for m in enum_type]
        msg = f"Invalid '{name}' entry in {data}, expecting one of [{', '.join(valid_values)}]: {enum_value}"
        raise ValueError(msg) from e


@dataclass(frozen=True)
class LSPConfigOptionV1:
    flag: str
    method: LSPPromptMethod
    prompt: str | None = None
    choices: list[str] | None = None
    default: str | None = None

    @classmethod
    def parse_all(cls, data: dict[str, Sequence[JsonValue]]) -> dict[str, list["LSPConfigOptionV1"]]:
        return {key: list(LSPConfigOptionV1.parse(item) for item in value) for (key, value) in data.items()}

    @classmethod
    def _extract_choices_field(cls, data: Mapping[str, JsonValue], prompt_method: LSPPromptMethod) -> list[str] | None:
        try:
            choices_unsafe = data["choices"]
        except KeyError as e:
            if prompt_method == LSPPromptMethod.CHOICE:
                raise ValueError(f"Missing 'choices' attribute in {data}") from e
            return None
        if not isinstance(choices_unsafe, list) or not all(isinstance(item, str) for item in choices_unsafe):
            msg = f"Invalid 'choices' entry in {data}, expecting a list of strings: {choices_unsafe}"
            raise ValueError(msg)
        return cast(list[str], choices_unsafe)

    @classmethod
    def parse(cls, data: JsonValue) -> "LSPConfigOptionV1":
        if not isinstance(data, dict):
            raise ValueError(f"Invalid transpiler config option, expecting a dict entry, got {data}")

        # Field extraction is factored out mainly to ensure the complexity of this method is not too high.

        flag = extract_string_field(data, "flag")
        method = extract_enum_field(data, "method", LSPPromptMethod)
        prompt = _maybe_extract_string_field(data, "prompt", is_required=method != LSPPromptMethod.FORCE)

        optional: dict[str, Any] = {}
        choices = cls._extract_choices_field(data, method)
        if choices is not None:
            optional["choices"] = choices

        default = _maybe_extract_string_field(data, "default", is_required=False)
        if default is not None:
            optional["default"] = default

        return LSPConfigOptionV1(flag, method, prompt, **optional)

    def is_optional(self) -> bool:
        # Semantics are currently that a value for an option is always required, except in the specific case of:
        #  - It being a QUESTION; AND
        #  - The default is set to the special "<none>" value.
        return self.method == LSPPromptMethod.QUESTION and self.default == self._question_optional_sentinel

    # Magic value that indicates no answer is required for a QUESTION prompt.
    _question_optional_sentinel = "<none>"

    def prompt_for_value(self, prompts: Prompts) -> JsonValue:
        if self.method == LSPPromptMethod.FORCE:
            return self.default
        assert self.prompt is not None
        if self.method == LSPPromptMethod.CONFIRM:
            return prompts.confirm(self.prompt)
        if self.method == LSPPromptMethod.QUESTION:
            # Hack to:
            #  - trick prompts.question() into indicating that no answer is required;
            #  - allow no answer to be given.
            # Normally prompts.confirm() requires an answer, or returns the default, and the default can't be None.
            # Note: LSP servers use '<none>' as a default to indicate that no answer is required.
            default = self.default if self.default else self._question_optional_sentinel
            result = prompts.question(self.prompt, default=default)
            if result == self._question_optional_sentinel:
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
    include_llm: bool = False
    catalog_name: str = "remorph"
    schema_name: str = "transpiler"
    transpiler_options: JsonValue = None

    @property
    def transpiler_config_path_parsed(self) -> Path | None:
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
    __version__ = 2

    tables: list[Table]

    @classmethod
    def v1_migrate(cls, raw: dict[str, Any]) -> dict[str, Any]:
        old_keys = ["source_catalog", "source_schema", "target_catalog", "target_schema"]
        for key in old_keys:
            raw.pop(key, None)
        raw["version"] = 2
        return raw


@dataclass(frozen=True)
class DatabaseConfig:
    """TODO remove. this was kept for backwards compatibility while migrating to ReconcileConfig v2"""

    source_catalog: str
    source_schema: str
    target_catalog: str
    target_schema: str


@dataclass
class SourceConnectionConfig:
    dialect: str
    catalog: str
    schema: str
    uc_connection_name: str | None = None

    def __post_init__(self):
        self.dialect = self.dialect.lower()
        if self.dialect != "databricks" and not self.uc_connection_name:
            raise ValueError(f"uc_connection_name is required for non-databricks sources (dialect={self.dialect})")


@dataclass
class TargetConnectionConfig:
    catalog: str
    schema: str


@dataclass
class HashExpressionOverrides:
    """Overrides for the row-hash function.

    Each value is raw SQL containing a single ``{}`` placeholder that the framework substitutes
    with the concatenated hash input. Whatever hash you pick on the source side must produce the
    same digest as the target side for the same input.
    """

    source: str
    target: str = "sha2({}, 256)"

    def __post_init__(self):
        for layer, expr in (("source", self.source), ("target", self.target)):
            if "{}" not in expr:
                raise ValueError(
                    f"hash_expression_overrides.{layer} must contain a '{{}}' placeholder for "
                    f"the hash input; got {expr!r}"
                )


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
class ReconcileMetadataConfig:
    catalog: str = "remorph"
    schema: str = "reconcile"
    volume: str = "reconcile_volume"


@dataclass
class ReconcileJobConfig:
    existing_cluster_id: str
    tags: dict[str, str]


@dataclass
class ReconcileConfig:
    __file__ = "reconcile.yml"
    __version__ = 2

    report_type: str
    source: SourceConnectionConfig
    target: TargetConnectionConfig
    metadata_config: ReconcileMetadataConfig
    job_overrides: ReconcileJobConfig | None = None
    hash_expression_overrides: HashExpressionOverrides | None = None

    def __post_init__(self):
        # Teradata has no out of the box cryptographic hash in pure SQL, so the user has to configure
        if self.source.dialect.lower() == "teradata" and not self.hash_expression_overrides:
            raise ValueError("Teradata source requires hash_expression_overrides to be configured ")

    @classmethod
    def v1_migrate(cls, raw: dict[str, Any]) -> dict[str, Any]:
        db_config = raw.pop("database_config")
        dialect = raw.pop("data_source")
        # Drop fields no longer recognised by the v2 schema.
        raw.pop("secret_scope", None)
        raw.pop("tables", None)

        source = {
            "dialect": dialect,
            # source_catalog was optional in v1 (Oracle service name could be omitted); default Oracle to ORCL.
            "catalog": db_config.get("source_catalog") or ("ORCL" if dialect == "oracle" else ""),
            "schema": db_config["source_schema"],
        }
        if dialect != "databricks":
            # Marker consumed by upgrade_reconcile_config_if_needed; user must supply the real UC connection name.
            source["uc_connection_name"] = "TODO"

        raw["source"] = source
        raw["target"] = {
            "catalog": db_config["target_catalog"],
            "schema": db_config["target_schema"],
        }
        raw["version"] = 2
        return raw

    @property
    def database_config(self) -> DatabaseConfig:
        """TODO remove. this was kept for backwards compatibility while migrating to ReconcileConfig v2"""
        return DatabaseConfig(
            source_catalog=self.source.catalog,
            source_schema=self.source.schema,
            target_catalog=self.target.catalog,
            target_schema=self.target.schema,
        )

    @property
    def table_recon_filename(self) -> str:
        """Canonical filename of the `TableRecon` config file in the install folder."""
        connection_or_catalog = self.source.uc_connection_name or self.source.catalog
        return f"recon_config_{self.source.dialect}_{connection_or_catalog}_{self.report_type}.json"


@dataclass
class LakebridgeConfiguration:
    transpile: TranspileConfig | None
    reconcile: ReconcileConfig | None
    # Temporary flag, indicating whether to include the LLM-based Switch transpiler.
    include_switch: bool = False
    # Internal: Use serverless compute for Switch job. Set via LAKEBRIDGE_CLUSTER_TYPE env var.
    switch_use_serverless: bool = True
