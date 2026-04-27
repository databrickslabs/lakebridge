from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Sequence, Union

import libcst as cst
import libcst.matchers as m

logger = logging.getLogger(__name__)

_AWSGLUE_MODULES = frozenset(
    ["awsglue.context", "awsglue.transforms", "awsglue.utils", "awsglue.dynamicframe", "awsglue.job"]
)

_UNSUPPORTED_TRANSFORMS = frozenset([
    "DropNullFields",
    "FillMissingValues",
    "Filter",
    "FindIncrementalMatches",
    "FlatMap",
    "Map",
    "Relationalize",
    "RenameField",
    "ResolveChoice",
    "SelectFields",
    "SelectFromCollection",
    "SplitFields",
    "SplitRows",
    "Unbox",
])

_GLUE_TYPE_MAP: dict[str, str] = {
    "string":    "string",
    "char":      "string",
    "varchar":   "string",
    "int":       "int",
    "integer":   "int",
    "long":      "bigint",
    "short":     "smallint",
    "byte":      "tinyint",
    "double":    "double",
    "float":     "float",
    "boolean":   "boolean",
    "bool":      "boolean",
    "binary":    "binary",
    "date":      "date",
    "timestamp": "timestamp",
    "decimal":   "decimal",
    "array":     "array",
    "map":       "map",
    "struct":    "struct",
}

_DECIMAL_RE = re.compile(r"^decimal\(\d+,\s*\d+\)$", re.IGNORECASE)

_ROLE_SPARK_CONTEXT = "SparkContext"
_ROLE_GLUE_CONTEXT = "GlueContext"
_ROLE_SPARK_SESSION = "SparkSession"
_ROLE_JOB = "Job"


def _map_glue_type(glue_type: str) -> str:
    lower = glue_type.lower().strip()
    if _DECIMAL_RE.match(lower):
        return lower
    return _GLUE_TYPE_MAP.get(lower, lower)


def _attr_chain(node: cst.BaseExpression) -> list[str]:
    parts: list[str] = []
    cur = node
    while isinstance(cur, cst.Attribute):
        parts.append(cur.attr.value)
        cur = cur.value
    if isinstance(cur, cst.Name):
        parts.append(cur.value)
    parts.reverse()
    return parts


def _kwarg_str(args: Sequence[cst.Arg], name: str) -> str | None:
    for arg in args:
        if arg.keyword and arg.keyword.value == name:
            if m.matches(arg.value, m.SimpleString()):
                assert isinstance(arg.value, cst.SimpleString)
                return arg.value.evaluated_value  # type: ignore[return-value]
    return None


def _kwarg_node(args: Sequence[cst.Arg], name: str) -> cst.BaseExpression | None:
    for arg in args:
        if arg.keyword and arg.keyword.value == name:
            return arg.value
    return None


def _kwarg_str_from_dict(dict_node: cst.BaseExpression | None, key: str) -> str | None:
    if not isinstance(dict_node, cst.Dict):
        return None
    for el in dict_node.elements:
        if isinstance(el, cst.DictElement) and m.matches(el.key, m.SimpleString()):
            assert isinstance(el.key, cst.SimpleString)
            if el.key.evaluated_value == key and isinstance(el.value, cst.SimpleString):
                return el.value.evaluated_value  # type: ignore[return-value]
    return None


def _first_positional(args: Sequence[cst.Arg]) -> cst.BaseExpression | None:
    for arg in args:
        if arg.keyword is None:
            return arg.value
    return None


def _parse_stmt(code: str) -> cst.SimpleStatementLine:
    module = cst.parse_module(code.strip() + "\n")
    assert isinstance(module.body[0], cst.SimpleStatementLine)
    return module.body[0]


def _parse_expr(code: str) -> cst.BaseExpression:
    module = cst.parse_module(code.strip())
    stmt = module.body[0]
    assert isinstance(stmt, cst.SimpleStatementLine)
    assert isinstance(stmt.body[0], cst.Expr)
    return stmt.body[0].value


class _BindingCollector(cst.CSTVisitor):
    """Read-only first pass: collect variable→role assignments before transformation."""

    def __init__(self) -> None:
        self.var_roles: dict[str, str] = {}

    def visit_Assign(self, node: cst.Assign) -> None:
        if not node.targets:
            return
        target = node.targets[0].target
        if not isinstance(target, cst.Name):
            return
        value = node.value
        for role in (_ROLE_SPARK_CONTEXT, _ROLE_GLUE_CONTEXT, _ROLE_JOB):
            if m.matches(value, m.Call(func=m.Name(role))):
                self.var_roles[target.value] = role
                return
        if m.matches(value, m.Attribute(attr=m.Name("spark_session"))):
            self.var_roles[target.value] = _ROLE_SPARK_SESSION


class _GlueVisitor(cst.CSTTransformer):
    def __init__(
        self,
        var_roles: dict[str, str],
        catalog: str | None = None,
        args_style: str = "argparse",
    ) -> None:
        super().__init__()
        self.warnings: list[str] = []

        self._var_roles = var_roles
        self._catalog = catalog
        self._args_style = args_style

        self._glue_context_var: str | None = next(
            (v for v, r in var_roles.items() if r == _ROLE_GLUE_CONTEXT), None
        )
        self._spark_session_var: str | None = next(
            (v for v, r in var_roles.items() if r == _ROLE_SPARK_SESSION), None
        )
        self._job_var: str | None = next(
            (v for v, r in var_roles.items() if r == _ROLE_JOB), None
        )

        self._needs_spark_session_import = False
        self._needs_argparse_import = False
        self._needs_col_import = False

        self._argparse_params: list[str] | None = None

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom | cst.RemovalSentinel:
        if not isinstance(updated_node.module, (cst.Attribute, cst.Name)):
            return updated_node
        module_str = ".".join(_attr_chain(updated_node.module))
        if module_str not in _AWSGLUE_MODULES:
            return updated_node
        if module_str == "awsglue.context":
            self._needs_spark_session_import = True
        if module_str == "awsglue.utils":
            self._needs_argparse_import = self._args_style == "argparse"
        return cst.RemovalSentinel.REMOVE

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        new_imports: list[cst.SimpleStatementLine] = []
        if self._needs_spark_session_import:
            new_imports.append(_parse_stmt("from pyspark.sql import SparkSession"))
        if self._needs_argparse_import:
            new_imports.append(_parse_stmt("import argparse"))
        if self._needs_col_import:
            new_imports.append(_parse_stmt("from pyspark.sql.functions import col"))
        if not new_imports:
            return updated_node

        # Insert after the last existing import, not at the top of the module.
        last_import_idx = -1
        for i, stmt in enumerate(updated_node.body):
            if isinstance(stmt, cst.SimpleStatementLine):
                if any(isinstance(s, (cst.Import, cst.ImportFrom)) for s in stmt.body):
                    last_import_idx = i

        insert_at = last_import_idx + 1
        body = list(updated_node.body)
        for offset, imp in enumerate(new_imports):
            body.insert(insert_at + offset, imp)
        return updated_node.with_changes(body=body)

    def leave_Assign(self, original_node: cst.Assign, updated_node: cst.Assign) -> cst.BaseSmallStatement:
        if not updated_node.targets:
            return updated_node
        target = updated_node.targets[0].target
        if not isinstance(target, cst.Name):
            return updated_node
        var_name = target.value
        value = updated_node.value
        role = self._var_roles.get(var_name)

        if role == _ROLE_SPARK_CONTEXT:
            return cst.RemovalSentinel.REMOVE

        if role == _ROLE_GLUE_CONTEXT:
            return cst.RemovalSentinel.REMOVE

        if role == _ROLE_SPARK_SESSION:
            new_value = _parse_expr("SparkSession.builder.getOrCreate()")
            return updated_node.with_changes(value=new_value)

        if role == _ROLE_JOB:
            return cst.RemovalSentinel.REMOVE

        if m.matches(value, m.Call(func=m.Name("getResolvedOptions"))):
            assert isinstance(value, cst.Call)
            return self._rewrite_get_resolved_options(updated_node, var_name, value)

        return updated_node

    def _rewrite_get_resolved_options(
        self, original: cst.Assign, var_name: str, call: cst.Call
    ) -> cst.BaseSmallStatement:
        args = call.args
        param_list_node = args[1].value if len(args) >= 2 else None
        if param_list_node is None:
            self.warnings.append("Could not find parameter list in getResolvedOptions call.")
            return original

        params: list[str] = []
        if isinstance(param_list_node, cst.List):
            for el in param_list_node.elements:
                if isinstance(el.value, cst.SimpleString):
                    s = el.value.evaluated_value  # type: ignore[assignment]
                    if s != "JOB_NAME":
                        params.append(str(s))
        else:
            self.warnings.append(
                "getResolvedOptions called with a non-literal parameter list; manual conversion required."
            )
            return original

        if self._args_style == "dbutils":
            self._argparse_params = params
            # Widget registration is deferred to leave_SimpleStatementLine so the
            # dbutils.widgets.text() calls are emitted before the dict comprehension.
            new_value = _parse_expr(f'{{k: dbutils.widgets.get(k) for k in {repr(params)}}}')
            return original.with_changes(value=new_value)

        self._argparse_params = params
        new_value = _parse_expr("vars(_parser.parse_args())")
        return original.with_changes(value=new_value)

    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> Union[cst.SimpleStatementLine, cst.RemovalSentinel]:
        if not updated_node.body:
            return cst.RemovalSentinel.REMOVE
        if isinstance(updated_node.body[0], cst.RemovalSentinel):
            return cst.RemovalSentinel.REMOVE

        # job.init() / job.commit() must be removed here rather than in leave_Assign
        # because they are expression statements, not assignments.
        if self._job_var and len(updated_node.body) == 1:
            stmt = updated_node.body[0]
            if isinstance(stmt, cst.Expr) and isinstance(stmt.value, cst.Call):
                call = stmt.value
                if isinstance(call.func, cst.Attribute):
                    chain = _attr_chain(call.func)
                    if len(chain) == 2 and chain[0] == self._job_var and chain[1] in ("init", "commit"):
                        return cst.RemovalSentinel.REMOVE

        if self._argparse_params is not None and isinstance(updated_node.body[0], cst.Assign):
            params = self._argparse_params
            self._argparse_params = None
            if self._args_style == "dbutils":
                prepend: list[cst.SimpleStatementLine] = []
                for p in params:
                    prepend.append(_parse_stmt(f'dbutils.widgets.text({repr(p)}, "")'))
                return cst.FlattenSentinel([*prepend, updated_node])
            else:
                prepend = [_parse_stmt("_parser = argparse.ArgumentParser()")]
                for p in params:
                    prepend.append(_parse_stmt(f'_parser.add_argument("--{p}")'))
                return cst.FlattenSentinel([*prepend, updated_node])

        return updated_node

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.BaseExpression:
        func = updated_node.func
        if not isinstance(func, cst.Attribute):
            return updated_node

        chain = _attr_chain(func)

        if (
            self._glue_context_var
            and len(chain) == 3
            and chain[0] == self._glue_context_var
            and chain[1] == "create_dynamic_frame"
            and chain[2] == "from_catalog"
        ):
            return self._rewrite_from_catalog(updated_node)

        if (
            self._glue_context_var
            and len(chain) == 3
            and chain[0] == self._glue_context_var
            and chain[1] == "create_dynamic_frame"
            and chain[2] == "from_options"
        ):
            return self._rewrite_from_options_read(updated_node)

        if (
            self._glue_context_var
            and len(chain) == 3
            and chain[0] == self._glue_context_var
            and chain[1] == "write_dynamic_frame"
            and chain[2] == "from_options"
        ):
            return self._rewrite_write_dynamic_frame(updated_node)

        if len(chain) == 2 and chain[0] == "ApplyMapping" and chain[1] == "apply":
            return self._rewrite_apply_mapping(updated_node)

        if chain[0] in _UNSUPPORTED_TRANSFORMS and len(chain) >= 2:
            self.warnings.append(f"Unsupported Glue transform '{chain[0]}' — manual conversion required.")
            return updated_node

        if len(chain) >= 2 and chain[-1] == "toDF":
            frame_arg = _first_positional(updated_node.args)
            if frame_arg is not None:
                return frame_arg
            if isinstance(func, cst.Attribute):
                return func.value

        if len(chain) >= 3 and chain[-2] == "DynamicFrame" and chain[-1] == "fromDF":
            df_arg = _first_positional(updated_node.args)
            if df_arg is not None:
                return df_arg

        return updated_node

    def _table_ref(self, database: str, table_name: str) -> str:
        if self._catalog:
            return repr(f"{self._catalog}.{database}.{table_name}")
        return repr(f"{database}.{table_name}")

    def _rewrite_from_catalog(self, node: cst.Call) -> cst.BaseExpression:
        database = _kwarg_str(node.args, "database")
        table_name = _kwarg_str(node.args, "table_name")
        push_down_predicate = _kwarg_str(node.args, "push_down_predicate")

        if database is None or table_name is None:
            self.warnings.append(
                "create_dynamic_frame.from_catalog with non-literal database/table_name — manual conversion required."
            )
            return node

        spark_var = self._spark_session_var or "spark"
        table_ref = self._table_ref(database, table_name)

        if push_down_predicate:
            return _parse_expr(f"{spark_var}.read.table({table_ref}).where({repr(push_down_predicate)})")
        return _parse_expr(f"{spark_var}.read.table({table_ref})")

    def _rewrite_from_options_read(self, node: cst.Call) -> cst.BaseExpression:
        connection_type = _kwarg_str(node.args, "connection_type")
        fmt = _kwarg_str(node.args, "format")
        spark_var = self._spark_session_var or "spark"
        conn_opts_node = _kwarg_node(node.args, "connection_options")

        if connection_type == "s3":
            path = _kwarg_str_from_dict(conn_opts_node, "path")
            read_format = fmt or "parquet"
            if path:
                return _parse_expr(f"{spark_var}.read.format({repr(read_format)}).load({repr(self._normalize_s3_path(path))})")
            return _parse_expr(f"{spark_var}.read.format({repr(read_format)}).load(...)")

        if connection_type == "jdbc":
            url = _kwarg_str_from_dict(conn_opts_node, "url")
            dbtable = _kwarg_str_from_dict(conn_opts_node, "dbtable")
            if url and dbtable:
                return _parse_expr(
                    f"{spark_var}.read.format('jdbc')"
                    f".option('url', {repr(url)})"
                    f".option('dbtable', {repr(dbtable)})"
                    f".load()"
                )
            self.warnings.append("JDBC read with dynamic options — manual conversion required.")
            return node

        self.warnings.append(
            f"create_dynamic_frame.from_options with connection_type={repr(connection_type)} — manual conversion required."
        )
        return node

    def _rewrite_write_dynamic_frame(self, node: cst.Call) -> cst.BaseExpression:
        connection_type = _kwarg_str(node.args, "connection_type")
        fmt = _kwarg_str(node.args, "format")
        frame_node = _kwarg_node(node.args, "frame")
        conn_opts_node = _kwarg_node(node.args, "connection_options")

        df_expr = "df"
        if frame_node is not None:
            df_expr = cst.parse_module("").code_for_node(frame_node)

        if connection_type == "s3":
            path = _kwarg_str_from_dict(conn_opts_node, "path")
            partition_keys: list[str] = []
            if isinstance(conn_opts_node, cst.Dict):
                for el in conn_opts_node.elements:
                    if isinstance(el, cst.DictElement) and m.matches(el.key, m.SimpleString()):
                        assert isinstance(el.key, cst.SimpleString)
                        if el.key.evaluated_value == "partitionKeys" and isinstance(el.value, cst.List):
                            for elem in el.value.elements:
                                if isinstance(elem.value, cst.SimpleString):
                                    pk = elem.value.evaluated_value
                                    if pk:
                                        partition_keys.append(str(pk))

            write_format = fmt or "parquet"
            chain = f"{df_expr}.write.format({repr(write_format)})"
            if partition_keys:
                keys_str = ", ".join(repr(k) for k in partition_keys)
                chain += f".partitionBy({keys_str})"
            if path:
                chain += f".save({repr(self._normalize_s3_path(path))})"
            else:
                chain += ".save(...)"
            return _parse_expr(chain)

        if connection_type == "jdbc":
            url = _kwarg_str_from_dict(conn_opts_node, "url")
            dbtable = _kwarg_str_from_dict(conn_opts_node, "dbtable")
            if url and dbtable:
                return _parse_expr(
                    f"{df_expr}.write.format('jdbc')"
                    f".option('url', {repr(url)})"
                    f".option('dbtable', {repr(dbtable)})"
                    f".save()"
                )
            self.warnings.append("JDBC write with dynamic options — manual conversion required.")
            return node

        self.warnings.append(
            f"write_dynamic_frame.from_options with connection_type={repr(connection_type)} — manual conversion required."
        )
        return node

    def _rewrite_apply_mapping(self, node: cst.Call) -> cst.BaseExpression:
        frame_node = _kwarg_node(node.args, "frame")
        mappings_node = _kwarg_node(node.args, "mappings")

        if frame_node is None:
            frame_node = _first_positional(node.args)
        if mappings_node is None and len(node.args) >= 2:
            mappings_node = node.args[1].value

        if frame_node is None:
            self.warnings.append("ApplyMapping.apply: could not determine frame — manual conversion required.")
            return node

        df_code = cst.parse_module("").code_for_node(frame_node)

        if not isinstance(mappings_node, cst.List):
            self.warnings.append("ApplyMapping.apply: non-literal mappings list — manual conversion required.")
            return node

        chain = df_code
        used_col = False
        for elem in mappings_node.elements:
            if not isinstance(elem.value, (cst.Tuple, cst.List)):
                self.warnings.append("ApplyMapping.apply: non-literal mapping tuple — skipping.")
                continue
            items = [e.value for e in elem.value.elements]
            if len(items) < 4:
                continue
            src_name_node, _src_type_node, dst_name_node, dst_type_node = items[:4]
            if not all(isinstance(n, cst.SimpleString) for n in [src_name_node, dst_name_node, dst_type_node]):
                self.warnings.append("ApplyMapping.apply: non-string mapping fields — skipping.")
                continue
            src_name = str(src_name_node.evaluated_value)  # type: ignore[union-attr]
            dst_name = str(dst_name_node.evaluated_value)  # type: ignore[union-attr]
            dst_type = str(dst_type_node.evaluated_value)  # type: ignore[union-attr]

            if src_name != dst_name:
                chain += f".withColumnRenamed({repr(src_name)}, {repr(dst_name)})"

            spark_type = _map_glue_type(dst_type)
            chain += f".withColumn({repr(dst_name)}, col({repr(dst_name)}).cast({repr(spark_type)}))"
            used_col = True

        if used_col:
            self._needs_col_import = True

        return _parse_expr(chain)

    @staticmethod
    def _normalize_s3_path(path: str) -> str:
        for prefix in ("s3a://", "s3n://"):
            if path.startswith(prefix):
                return "s3://" + path[len(prefix):]
        return path


class GlueTransformer:
    def __init__(
        self,
        file_path: Path | None = None,
        catalog: str | None = None,
        args_style: str = "argparse",
    ) -> None:
        self._file_path = file_path
        self._catalog = catalog
        self._args_style = args_style

    def transform(self, source_code: str) -> tuple[str, list[str]]:
        tree = cst.parse_module(source_code)

        collector = _BindingCollector()
        tree.visit(collector)

        visitor = _GlueVisitor(collector.var_roles, catalog=self._catalog, args_style=self._args_style)
        modified = tree.visit(visitor)
        return modified.code, visitor.warnings
