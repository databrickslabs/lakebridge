import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import attrs
from lsprotocol import types as types_module
from lsprotocol.types import (
    INITIALIZE,
    METHOD_TO_TYPES,
    TEXT_DOCUMENT_DID_CLOSE,
    TEXT_DOCUMENT_DID_OPEN,
    Diagnostic,
    DiagnosticSeverity,
    DidCloseTextDocumentParams,
    DidOpenTextDocumentParams,
    InitializeParams,
    LanguageKind,
    Position,
    Range,
    Registration,
    RegistrationParams,
    TextEdit,
)
from pygls.lsp.server import LanguageServer

logging.basicConfig(filename='test-lsp-server.log', filemode='w', level=logging.DEBUG)

logger = logging.getLogger(__name__)

# the below code also exists in lsp_engine.py
# it will be factorized as part of https://github.com/databrickslabs/remorph/issues/1304
TRANSPILE_TO_DATABRICKS_METHOD = "document/transpileToDatabricks"
TRANSPILE_TO_DATABRICKS_CAPABILITY = {"id": str(uuid4()), "method": TRANSPILE_TO_DATABRICKS_METHOD}


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
    language_id: LanguageKind | str = attrs.field()  #
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


class TestLspServer(LanguageServer):

    def __init__(self, name, version):
        super().__init__(name, version)
        self.initialization_options: Any = None

    @property
    def dialect(self) -> str:
        return self.initialization_options["remorph"]["source-dialect"]

    @property
    def experimental(self) -> str | None:
        options = self.initialization_options.get("options", {}) or {}
        return options.get("-experimental", None)

    @property
    def whatever(self) -> str | None:
        custom = self.initialization_options.get("custom", {}) or {}
        return custom.get("whatever", None)

    async def did_initialize(self, init_params: InitializeParams) -> None:
        self.initialization_options = init_params.initialization_options or {}
        client_info = init_params.client_info
        if client_info:
            logger.debug(f"client-info={client_info.name}/{client_info.version}")
        if init_params.process_id:
            logger.debug(f"client-process-id={init_params.process_id}")
        logger.debug(f"dialect={self.dialect}")
        logger.debug(f"whatever={self.whatever}")
        logger.debug(f"experimental={self.experimental}")
        # TODO check whether the client supports dynamic registration
        registrations = [
            Registration(
                id=TRANSPILE_TO_DATABRICKS_CAPABILITY["id"], method=TRANSPILE_TO_DATABRICKS_CAPABILITY["method"]
            )
        ]
        register_params = RegistrationParams(registrations)
        await self.client_register_capability_async(register_params)
        # ensure we can fetch a workspace file
        uri = self.workspace.root_uri + "/workspace_file.yml"
        doc = self.workspace.get_text_document(uri)
        logger.debug(f"fetch-document-uri={uri}: {doc.source}")

    def transpile_to_databricks(self, params: TranspileDocumentParams) -> TranspileDocumentResult:
        source_sql = self.workspace.get_text_document(params.uri).source
        source_lines = source_sql.split("\n")
        range = Range(start=Position(0, 0), end=Position(len(source_lines), len(source_lines[-1])))
        transpiled_sql, diagnostics = self._transpile(Path(params.uri).name, range, source_sql)
        changes = [TextEdit(range=range, new_text=transpiled_sql)]
        return TranspileDocumentResult(
            uri=params.uri, language_id=LanguageKind.Sql, changes=changes, diagnostics=diagnostics
        )

    def _transpile(self, file_name: str, lsp_range: Range, source_sql: str) -> tuple[str, list[Diagnostic]]:
        if file_name == "no_transpile.sql":
            diagnostic = Diagnostic(
                range=lsp_range,
                message="No transpilation required",
                severity=DiagnosticSeverity.Information,
                code="GENERATION-NOT_REQUIRED",
            )
            return source_sql, [diagnostic]
        elif file_name == "unsupported_lca.sql":
            diagnostic = Diagnostic(
                range=lsp_range,
                message="LCA conversion not supported",
                severity=DiagnosticSeverity.Error,
                code="ANALYSIS-UNSUPPORTED_LCA",
            )
            return source_sql, [diagnostic]
        elif file_name == "internal.sql":
            diagnostic = Diagnostic(
                range=lsp_range,
                message="Something went wrong",
                severity=DiagnosticSeverity.Warning,
                code="SOME_ERROR_CODE",
            )
            return source_sql, [diagnostic]
        elif file_name == "workflow.xml":
            combined = Path(__file__).parent / "aggregated_output.mime"
            output = combined.read_text("utf-8")
            return output, []
        else:
            # general test case
            return source_sql.upper(), []


server = TestLspServer("test-lsp-server", "v0.1")


@server.feature(INITIALIZE)
async def lsp_did_initialize(params: InitializeParams) -> None:
    await server.did_initialize(params)


@server.feature(TEXT_DOCUMENT_DID_OPEN)
async def lsp_text_document_did_open(params: DidOpenTextDocumentParams) -> None:
    logger.debug(f"open-document-uri={params.text_document.uri}")


@server.feature(TEXT_DOCUMENT_DID_CLOSE)
async def lsp_text_document_did_close(params: DidCloseTextDocumentParams) -> None:
    logger.debug(f"close-document-uri={params.text_document.uri}")


@server.feature(TRANSPILE_TO_DATABRICKS_METHOD)
def transpile_to_databricks(params: TranspileDocumentParams) -> TranspileDocumentResult:
    return server.transpile_to_databricks(params)


if __name__ == "__main__":
    # added for testing logging of stderr output
    sys.stderr.write("Running LSP Test Server\u2026\n")
    sys.stderr.flush()
    sys.stderr.buffer.write(b"Some bytes that are invalid UTF-8: [\xc0\xc0]\n")
    sys.stderr.buffer.flush()
    logger.debug(f"SOME_ENV={os.getenv('SOME_ENV')}")
    logger.debug(f"sys.args={sys.argv}")
    server.start_io()
