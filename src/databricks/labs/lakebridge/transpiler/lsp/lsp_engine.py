from __future__ import annotations

import abc
import asyncio
import logging
import os
import shutil
import sys
import venv
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import attrs
import yaml
from lsprotocol import types as types_module
from lsprotocol.types import (
    CLIENT_REGISTER_CAPABILITY,
    METHOD_TO_TYPES,
    ClientCapabilities,
    ClientInfo,
    Diagnostic,
    DiagnosticSeverity,
    DidCloseTextDocumentParams,
    DidOpenTextDocumentParams,
    InitializeParams,
    InitializeResult,
    LanguageKind,
)
from lsprotocol.types import Position as LSPPosition
from lsprotocol.types import Range as LSPRange
from lsprotocol.types import Registration, RegistrationParams, TextDocumentIdentifier, TextDocumentItem, TextEdit
from pygls.exceptions import FeatureRequestError
from pygls.lsp.client import BaseLanguageClient

from databricks.labs.blueprint.installation import JsonValue, RootJsonValue
from databricks.labs.blueprint.wheels import ProductInfo
from databricks.labs.lakebridge.config import LSPConfigOptionV1, TranspileConfig, TranspileResult, extract_string_field
from databricks.labs.lakebridge.errors.exceptions import IllegalStateException
from databricks.labs.lakebridge.helpers.file_utils import is_dbt_project_file, is_sql_file
from databricks.labs.lakebridge.transpiler.transpile_engine import TranspileEngine
from databricks.labs.lakebridge.transpiler.transpile_status import (
    CodePosition,
    CodeRange,
    ErrorKind,
    ErrorSeverity,
    TranspileError,
)

logger = logging.getLogger(__name__)


def _is_all_strings(values: Iterable[object]) -> bool:
    """Typeguard, to check if all values in the iterable are strings."""
    return all(isinstance(x, str) for x in values)


def _is_all_sequences(values: Iterable[object]) -> bool:
    """Typeguard, to check if all values in the iterable are sequences."""
    return all(isinstance(x, Sequence) for x in values)


@dataclass
class _LSPRemorphConfigV1:
    name: str
    dialects: Sequence[str]
    env_vars: Mapping[str, str]
    command_line: Sequence[str]

    @classmethod
    def parse(cls, data: Mapping[str, JsonValue]) -> _LSPRemorphConfigV1:
        cls._check_version(data)
        name = extract_string_field(data, "name")
        dialects = cls._extract_dialects(data)
        env_vars = cls._extract_env_vars(data)
        command_line = cls._extract_command_line(data)
        return _LSPRemorphConfigV1(name, dialects, env_vars, command_line)

    @classmethod
    def _check_version(cls, data: Mapping[str, JsonValue]) -> None:
        try:
            version = data["version"]
        except KeyError as e:
            raise ValueError("Missing 'version' attribute") from e
        if version != 1:
            raise ValueError(f"Unsupported transpiler config version: {version}")

    @classmethod
    def _extract_dialects(cls, data: Mapping[str, JsonValue]) -> Sequence[str]:
        try:
            dialects_unsafe = data["dialects"]
        except KeyError as e:
            raise ValueError("Missing 'dialects' attribute") from e
        if not isinstance(dialects_unsafe, list) or not dialects_unsafe or not _is_all_strings(dialects_unsafe):
            msg = f"Invalid 'dialects' attribute, expected a non-empty list of strings but got: {dialects_unsafe}"
            raise ValueError(msg)
        return cast(list[str], dialects_unsafe)

    @classmethod
    def _extract_env_vars(cls, data: Mapping[str, JsonValue]) -> Mapping[str, str]:
        try:
            env_vars_unsafe = data["environment"]
            if not isinstance(env_vars_unsafe, Mapping) or not _is_all_strings(env_vars_unsafe.values()):
                msg = f"Invalid 'environment' entry, expected a mapping with string values but got: {env_vars_unsafe}"
                raise ValueError(msg)
            return cast(dict[str, str], env_vars_unsafe)
        except KeyError:
            return {}

    @classmethod
    def _extract_command_line(cls, data: Mapping[str, JsonValue]) -> Sequence[str]:
        try:
            command_line = data["command_line"]
        except KeyError as e:
            raise ValueError("Missing 'command_line' attribute") from e
        if not isinstance(command_line, list) or not command_line or not _is_all_strings(command_line):
            msg = f"Invalid 'command_line' attribute, expected a non-empty list of strings but got: {command_line}"
            raise ValueError(msg)
        return cast(list[str], command_line)


@dataclass
class LSPConfig:
    path: Path
    remorph: _LSPRemorphConfigV1
    options: Mapping[str, Sequence[LSPConfigOptionV1]]
    custom: Mapping[str, JsonValue]

    @property
    def name(self):
        return self.remorph.name

    def options_for_dialect(self, source_dialect: str) -> Sequence[LSPConfigOptionV1]:
        return [*self.options.get("all", []), *self.options.get(source_dialect, [])]

    @classmethod
    def load(cls, path: Path) -> LSPConfig:
        yaml_text = path.read_text()
        data: RootJsonValue = yaml.safe_load(yaml_text)
        if not isinstance(data, Mapping):
            msg = f"Invalid transpiler configuration, expecting a root object but got: {data}"
            raise ValueError(msg)

        remorph = cls._extract_remorph_data(data)
        options = cls._extract_options(data)
        custom = cls._extract_custom(data)
        return LSPConfig(path, remorph, options, custom)

    @classmethod
    def _extract_remorph_data(cls, data: Mapping[str, JsonValue]) -> _LSPRemorphConfigV1:
        try:
            remorph_data = data["remorph"]
        except KeyError as e:
            raise ValueError("Missing 'remorph' attribute") from e
        if not isinstance(remorph_data, Mapping):
            msg = f"Invalid transpiler config, 'remorph' entry must be an object but got: {remorph_data}"
            raise ValueError(msg)
        return _LSPRemorphConfigV1.parse(remorph_data)

    @classmethod
    def _extract_options(cls, data: Mapping[str, JsonValue]) -> Mapping[str, Sequence[LSPConfigOptionV1]]:
        try:
            options_data_unsfe = data["options"]
        except KeyError:
            # Optional, so no problem if missing
            return {}
        if not isinstance(options_data_unsfe, Mapping) or not _is_all_sequences(options_data_unsfe.values()):
            msg = f"Invalid transpiler config, 'options' must be an object with list properties but got: {options_data_unsfe}"
            raise ValueError(msg)
        options_data = cast(dict[str, Sequence[JsonValue]], options_data_unsfe)
        return LSPConfigOptionV1.parse_all(options_data)

    @classmethod
    def _extract_custom(cls, data: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        try:
            custom = data["custom"]
            if not isinstance(custom, Mapping):
                msg = f"Invalid 'custom' entry, expected a mapping but got: {custom}"
                raise ValueError(msg)
            return custom
        except KeyError:
            # Optional, so no problem if missing
            return {}


def lsp_feature(name: str, options: Any | None = None):
    def wrapped(func: Callable):
        _LSP_FEATURES.append((name, options, func))
        return func

    return wrapped


_LSP_FEATURES: list[tuple[str, Any | None, Callable]] = []

# the below code also exists in lsp_server.py
# it will be factorized as part of https://github.com/databrickslabs/remorph/issues/1304
TRANSPILE_TO_DATABRICKS_METHOD = "document/transpileToDatabricks"


@attrs.define
class TranspileDocumentParams:
    uri: str = attrs.field()
    language_id: LanguageKind | str = attrs.field()


@attrs.define
class TranspileDocumentRequest:
    # 'id' is mandated by LSP
    # pylint: disable=invalid-name
    id: int | str = attrs.field()
    params: TranspileDocumentParams = attrs.field()
    method: Literal["document/transpileToDatabricks"] = "document/transpileToDatabricks"
    jsonrpc: str = attrs.field(default="2.0")


@attrs.define
class TranspileDocumentResult:
    uri: str = attrs.field()
    language_id: LanguageKind | str = attrs.field()
    changes: Sequence[TextEdit] = attrs.field()
    diagnostics: Sequence[Diagnostic] = attrs.field()


@attrs.define
class TranspileDocumentResponse:
    # 'id' is mandated by LSP
    # pylint: disable=invalid-name
    id: int | str = attrs.field()
    result: TranspileDocumentResult = attrs.field()
    jsonrpc: str = attrs.field(default="2.0")


def install_special_properties():
    is_special_property = getattr(types_module, "is_special_property")

    def customized(cls: type, property_name: str) -> bool:
        if cls is TranspileDocumentRequest and property_name in {"method", "jsonrpc"}:
            return True
        return is_special_property(cls, property_name)

    setattr(types_module, "is_special_property", customized)


install_special_properties()

METHOD_TO_TYPES[TRANSPILE_TO_DATABRICKS_METHOD] = (
    TranspileDocumentRequest,
    TranspileDocumentResponse,
    TranspileDocumentParams,
    None,
)


# subclass BaseLanguageClient so we can override stuff when required
class _LanguageClient(BaseLanguageClient):

    def __init__(self, name: str, version: str) -> None:
        super().__init__(name, version)
        self._transpile_to_databricks_capability: Registration | None = None
        self._register_lsp_features()

    @property
    def is_alive(self):
        return self._server and self._server.returncode is None

    @property
    def transpile_to_databricks_capability(self):
        return self._transpile_to_databricks_capability

    @lsp_feature(CLIENT_REGISTER_CAPABILITY)
    def register_capabilities(self, params: RegistrationParams) -> None:
        for registration in params.registrations:
            if registration.method == TRANSPILE_TO_DATABRICKS_METHOD:
                logger.debug(f"Registered capability: {registration.method}")
                self._transpile_to_databricks_capability = registration
                continue
            logger.debug(f"Unknown capability: {registration.method}")

    async def transpile_document_async(self, params: TranspileDocumentParams) -> TranspileDocumentResult:
        """Transpile a document to Databricks SQL.

        The caller is responsible for ensuring that the LSP server is capable of handling this request.

        Args:
            params: The parameters for the transpile request to forward to the LSP server.
        Returns:
            The result of the transpile request, from the LSP server.
        Raises:
            IllegalStateException: If the client has been stopped or the server hasn't (yet) signalled that it is
                capable of transpiling documents to Databricks SQL.
        """
        if self.stopped:
            raise IllegalStateException("Client has been stopped.")
        if not self.transpile_to_databricks_capability:
            raise IllegalStateException("Client has not yet registered its transpile capability.")
        return await self.protocol.send_request_async(TRANSPILE_TO_DATABRICKS_METHOD, params)

    # can't use @client.feature because it requires a global instance
    def _register_lsp_features(self):
        for name, options, func in _LSP_FEATURES:
            decorator = self.protocol.fm.feature(name, options)
            wrapper = self._wrapper_for_lsp_feature(func)
            decorator(wrapper)

    def _wrapper_for_lsp_feature(self, func):
        def wrapper(params):
            return func(self, params)

        return wrapper

    async def start_io(self, cmd: str, *args, **kwargs):
        await super().start_io(cmd, *args, **kwargs)
        # forward stderr
        task = asyncio.create_task(self.pipe_stderr())
        self._async_tasks.append(task)

    async def pipe_stderr(self) -> None:
        server = self._server
        assert server is not None
        stderr = server.stderr
        assert stderr is not None
        while not self._stop_event.is_set():
            data: bytes = await stderr.readline()
            if not data:
                return
            # Invalid UTF-8 isn't great, but we can at least log it with the replacement character rather
            # than dropping it silently or triggering an exception.
            message = data.decode("utf-8", errors="replace").strip()
            # Although information may arrive via stderr, it's generally informational in nature and doesn't
            # necessarily represent an error
            # TODO: analyze message and log it accordingly (info/war/error...).
            logger.info(message)
            if not data.endswith(b"\n"):
                break


class ChangeManager(abc.ABC):

    @classmethod
    def apply(
        cls, source_code: str, changes: Sequence[TextEdit], diagnostics: Sequence[Diagnostic], file_path: Path
    ) -> TranspileResult:
        if not changes and not diagnostics:
            return TranspileResult(source_code, 1, [])
        transpile_errors = [DiagnosticConverter.apply(file_path, diagnostic) for diagnostic in diagnostics]
        try:
            lines = source_code.split("\n")
            for change in changes:
                lines = cls._apply(lines, change)
            transpiled_code = "\n".join(lines)
            return TranspileResult(transpiled_code, 1, transpile_errors)
        except IndexError as e:
            logger.error("Failed to apply changes", exc_info=e)
            error = TranspileError(
                code="INTERNAL_ERROR",
                kind=ErrorKind.INTERNAL,
                severity=ErrorSeverity.ERROR,
                path=file_path,
                message="Internal error, failed to apply changes",
            )
            transpile_errors.append(error)
            return TranspileResult(source_code, 1, transpile_errors)

    @classmethod
    def _apply(cls, lines: list[str], change: TextEdit) -> list[str]:
        new_lines = change.new_text.split("\n")
        if cls._is_full_document_change(lines, change):
            return new_lines
        # keep lines before
        result: list[str] = [] if change.range.start.line <= 0 else lines[0 : change.range.start.line]
        # special case where change covers full lines
        if change.range.start.character <= 0 and change.range.end.character >= len(lines[change.range.end.line]):
            pass
        # special case where change is within 1 line
        elif change.range.start.line == change.range.end.line:
            old_line = lines[change.range.start.line]
            if change.range.start.character > 0:
                new_lines[0] = old_line[0 : change.range.start.character] + new_lines[0]
            if change.range.end.character < len(old_line):
                new_lines[-1] += old_line[change.range.end.character :]
        else:
            if change.range.start.character > 0:
                old_line = lines[change.range.start.line]
                new_lines[0] = old_line[0 : change.range.start.character] + new_lines[0]
            if change.range.end.character < len(lines[change.range.end.line]):
                old_line = lines[change.range.end.line]
                new_lines[-1] += old_line[change.range.end.character :]
        result.extend(new_lines)
        # keep lines after
        if change.range.end.line < len(lines) - 1:
            result.extend(lines[change.range.end.line + 1 :])
        return result

    @classmethod
    def _is_full_document_change(cls, lines: list[str], change: TextEdit) -> bool:
        # A range's end is exclusive. Therefore full document range goes from (0, 0) to (l, 0) where l is the number
        # of lines in the document.
        return (
            change.range.start.line == 0
            and change.range.start.character == 0
            and change.range.end.line >= len(lines)
            and change.range.end.character >= 0
        )


class DiagnosticConverter(abc.ABC):

    _KIND_NAMES = {e.name for e in ErrorKind}

    @classmethod
    def apply(cls, file_path: Path, diagnostic: Diagnostic) -> TranspileError:
        code = str(diagnostic.code)
        kind = ErrorKind.INTERNAL
        parts = code.split("-")
        if len(parts) >= 2 and parts[0] in cls._KIND_NAMES:
            kind = ErrorKind[parts[0]]
            parts.pop(0)
            code = "-".join(parts)
        severity = cls._convert_severity(diagnostic.severity)
        lsp_range = cls._convert_range(diagnostic.range)
        return TranspileError(
            code=code, kind=kind, severity=severity, path=file_path, message=diagnostic.message, range=lsp_range
        )

    @classmethod
    def _convert_range(cls, lsp_range: LSPRange | None) -> CodeRange | None:
        if not lsp_range:
            return None
        return CodeRange(cls._convert_position(lsp_range.start), cls._convert_position(lsp_range.end))

    @classmethod
    def _convert_position(cls, lsp_position: LSPPosition) -> CodePosition:
        return CodePosition(lsp_position.line, lsp_position.character)

    @classmethod
    def _convert_severity(cls, severity: DiagnosticSeverity | None) -> ErrorSeverity:
        if severity == DiagnosticSeverity.Information:
            return ErrorSeverity.INFO
        if severity == DiagnosticSeverity.Warning:
            return ErrorSeverity.WARNING
        if severity == DiagnosticSeverity.Error:
            return ErrorSeverity.ERROR
        return ErrorSeverity.INFO


class LSPEngine(TranspileEngine):

    @classmethod
    def from_config_path(cls, config_path: Path) -> LSPEngine:
        config = LSPConfig.load(config_path)
        return LSPEngine(config_path.parent, config)

    @classmethod
    def client_metadata(cls) -> tuple[str, str]:
        """Obtain the name and version for this LSP client, respectively in a tuple."""
        product_info = ProductInfo.from_class(cls)
        return product_info.product_name(), product_info.version()

    def __init__(self, workdir: Path, config: LSPConfig) -> None:
        self._workdir = workdir
        self._config = config
        name, version = self.client_metadata()
        self._client = _LanguageClient(name, version)
        self._init_response: InitializeResult | None = None

    @property
    def transpiler_name(self) -> str:
        return self._config.name

    def options_for_dialect(self, source_dialect: str) -> Sequence[LSPConfigOptionV1]:
        """Get the options supported when transpiling a given source dialect."""
        return self._config.options_for_dialect(source_dialect)

    @property
    def supported_dialects(self) -> Sequence[str]:
        return self._config.remorph.dialects

    @property
    def server_has_transpile_capability(self) -> bool:
        return self._client.transpile_to_databricks_capability is not None

    async def initialize(self, config: TranspileConfig) -> None:
        if self.is_alive:
            raise IllegalStateException("LSP engine is already initialized")
        try:
            await self._do_initialize(config)
            await self._await_for_transpile_capability()
        # it is good practice to catch broad exceptions raised by launching a child process
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("LSP initialization failed", exc_info=e)

    async def _do_initialize(self, config: TranspileConfig) -> None:
        await self._start_server()
        input_path = config.input_path
        root_path = input_path if input_path.is_dir() else input_path.parent
        params = InitializeParams(
            capabilities=self._client_capabilities(),
            client_info=ClientInfo(name=self._client.name, version=self._client.version),
            process_id=os.getpid(),
            root_uri=str(root_path.absolute().as_uri()),
            workspace_folders=None,  # for now, we only support a single workspace = root_uri
            initialization_options=self._initialization_options(config),
        )
        logger.debug(f"LSP init params: {params}")
        self._init_response = await self._client.initialize_async(params)

    async def _start_server(self) -> None:
        # Sanity-check and split the command-line into components.
        if not (command_line := self._config.remorph.command_line):
            raise ValueError(f"Missing command line for LSP server: {self._config.path}")
        executable, *args = command_line

        # Extract the environment, preparing to ensure that PATH is set correctly.
        env: dict[str, str] = os.environ | self._config.remorph.env_vars
        path = env.get("PATH", os.defpath)

        # If we have a virtual environment, ensure the bin directory is first on the PATH. This normally takes
        # care of python executables, but also deals with any entry-points that the LSP server might install.
        if (venv_path := self._workdir / ".venv").exists():
            executable, additional_path = self._activate_venv(venv_path, executable)
            # Ensure PATH is in sync with the search path we will use to locate the LSP server executable.
            env["PATH"] = path = f"{additional_path}{os.pathsep}{path}"
        logger.debug(f"Using PATH for launching LSP server: {path}")

        # Locate the LSP server executable in a platform-independent way.
        # Reference: https://docs.python.org/3/library/subprocess.html#popen-constructor
        executable = shutil.which(executable, path=path) or executable

        await self._launch_executable(executable, args, env)

    @staticmethod
    def _activate_venv(venv_path: Path, executable: str) -> tuple[str, Path]:
        """Obtain the bin/script directory for the virtual environment, to extend the search path."""
        logger.debug(f"Detected virtual environment to use at: {venv_path}")
        use_symlinks = sys.platform != "win32"
        builder = venv.EnvBuilder(symlinks=use_symlinks)
        context = builder.ensure_directories(venv_path)

        # Workaround for Windows, where bin_path (Scripts/) doesn't contain python3.exe: if the executable is python
        # or python3, we substitute it for what is needed to launch the venv's python interpreter.
        if os.path.normcase(executable) in {"python", "python3"}:
            executable = context.env_exec_cmd

        return executable, context.bin_path

    async def _launch_executable(self, executable: str, args: Sequence[str], env: Mapping[str, str]) -> None:
        log_level = logging.getLevelName(logging.getLogger("databricks").level)
        # TODO: Remove the --log_level argument once all our transpilers support the environment variable.
        args = [*args, f"--log_level={log_level}"]
        env = {**env, "DATABRICKS_LAKEBRIDGE_LOG_LEVEL": log_level}
        logger.debug(f"Starting LSP engine: {executable} {args} (cwd={self._workdir})")
        await self._client.start_io(executable, *args, env=env, cwd=self._workdir)

    def _client_capabilities(self):
        return ClientCapabilities()  # TODO do we need to refine this ?

    def _initialization_options(self, config: TranspileConfig):
        return {
            "remorph": {
                "source-dialect": config.source_dialect,
            },
            "options": config.transpiler_options,
            "custom": self._config.custom,
        }

    async def _await_for_transpile_capability(self):
        for _ in range(1, 100):
            if self._client.transpile_to_databricks_capability:
                return
            await asyncio.sleep(0.1)
        if not self._client.transpile_to_databricks_capability:
            msg = f"LSP server did not register its {TRANSPILE_TO_DATABRICKS_METHOD} capability"
            raise FeatureRequestError(msg)

    async def shutdown(self):
        await self._client.shutdown_async(None)
        self._client.exit(None)
        await self._client.stop()

    @property
    def is_alive(self):
        return self._client.is_alive

    async def transpile(
        self, source_dialect: str, target_dialect: str, source_code: str, file_path: Path
    ) -> TranspileResult:
        self.open_document(file_path, source_code=source_code)
        response = await self.transpile_document(file_path)
        self.close_document(file_path)
        return ChangeManager.apply(source_code, response.changes, response.diagnostics, file_path)

    def open_document(self, file_path: Path, source_code: str) -> None:
        text_document = TextDocumentItem(
            uri=file_path.absolute().as_uri(), language_id=LanguageKind.Sql, version=1, text=source_code
        )
        params = DidOpenTextDocumentParams(text_document)
        self._client.text_document_did_open(params)

    def close_document(self, file_path: Path) -> None:
        text_document = TextDocumentIdentifier(uri=file_path.absolute().as_uri())
        params = DidCloseTextDocumentParams(text_document)
        self._client.text_document_did_close(params)

    async def transpile_document(self, file_path: Path) -> TranspileDocumentResult:
        params = TranspileDocumentParams(uri=file_path.absolute().as_uri(), language_id=LanguageKind.Sql)
        result = await self._client.transpile_document_async(params)
        return result

    # TODO infer the below from config file
    def is_supported_file(self, file: Path) -> bool:
        if self._is_bladebridge() or self._is_test_transpiler():
            return True
        if self._is_morpheus():
            return is_sql_file(file) or is_dbt_project_file(file)
        # then only support sql
        return is_sql_file(file)

    # TODO remove this
    def _is_test_transpiler(self):
        return self._config.remorph.name == "test-transpiler"

    # TODO remove this
    def _is_bladebridge(self):
        return self._config.remorph.name == "Bladebridge"

    # TODO remove this
    def _is_morpheus(self):
        return self._config.remorph.name == "Morpheus"
