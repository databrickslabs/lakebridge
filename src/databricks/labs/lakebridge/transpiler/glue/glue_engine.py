from __future__ import annotations

import ast as _ast
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from databricks.labs.lakebridge.config import TranspileConfig, TranspileResult
from databricks.labs.lakebridge.transpiler.transpile_engine import TranspileEngine
from databricks.labs.lakebridge.transpiler.transpile_status import (
    ErrorKind,
    ErrorSeverity,
    TranspileError,
)
from databricks.labs.lakebridge.transpiler.glue.glue_transformer import GlueTransformer

logger = logging.getLogger(__name__)

_DEFAULT_ARGS_STYLE = "argparse"


def _extract_options(config: TranspileConfig) -> tuple[str | None, str]:
    """Extract catalog and args_style from transpiler_options mapping."""
    opts = config.transpiler_options
    if not isinstance(opts, Mapping):
        return None, _DEFAULT_ARGS_STYLE
    catalog = opts.get("catalog") or None
    args_style = str(opts.get("args-style", _DEFAULT_ARGS_STYLE))
    if args_style not in ("argparse", "dbutils"):
        logger.warning("Unknown args-style %r, falling back to 'argparse'.", args_style)
        args_style = _DEFAULT_ARGS_STYLE
    return catalog, args_style


class GlueEngine(TranspileEngine):
    """Transpiles AWS Glue PySpark scripts to Databricks PySpark."""

    def __init__(self) -> None:
        self._catalog: str | None = None
        self._args_style: str = _DEFAULT_ARGS_STYLE

    @property
    def transpiler_name(self) -> str:
        return "glue"

    @property
    def supported_dialects(self) -> Sequence[str]:
        return ["glue"]

    def is_supported_file(self, file: Path) -> bool:
        return file.suffix.lower() == ".py"

    async def initialize(self, config: TranspileConfig) -> None:
        self._catalog, self._args_style = _extract_options(config)

    async def shutdown(self) -> None:
        pass

    async def transpile(
        self,
        source_dialect: str,
        target_dialect: str,
        source_code: str,
        file_path: Path,
    ) -> TranspileResult:
        try:
            transformer = GlueTransformer(
                file_path,
                catalog=self._catalog,
                args_style=self._args_style,
            )
            transpiled_code, warnings = transformer.transform(source_code)

            try:
                _ast.parse(transpiled_code)
            except SyntaxError as syn_err:
                warnings.append(f"Generated code contains a syntax error: {syn_err}")

            errors = [
                TranspileError(
                    code="GLUE_WARNING",
                    kind=ErrorKind.GENERATION,
                    severity=ErrorSeverity.WARNING,
                    path=file_path,
                    message=msg,
                )
                for msg in warnings
            ]
            return TranspileResult(transpiled_code, 1, errors)

        except SyntaxError as err:
            error = TranspileError(
                code="SYNTAX_ERROR",
                kind=ErrorKind.PARSING,
                severity=ErrorSeverity.ERROR,
                path=file_path,
                message=f"Python syntax error in source: {err}",
            )
            return TranspileResult(source_code, 0, [error])

        except Exception as err:  # pylint: disable=broad-exception-caught
            logger.exception("Unexpected error transpiling %s", file_path)
            error = TranspileError(
                code="GLUE_TRANSPILE_ERROR",
                kind=ErrorKind.GENERATION,
                severity=ErrorSeverity.ERROR,
                path=file_path,
                message=f"Unexpected transpilation error: {err}",
            )
            return TranspileResult(source_code, 0, [error])
