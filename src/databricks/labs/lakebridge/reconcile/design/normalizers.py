import dataclasses
from abc import ABC, abstractmethod

import expressions as e
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from utypes import ExternalType, UType, ColumnTypeName


@dataclasses.dataclass(frozen=True)
class ExternalColumnDefinition:
    column_name: str
    data_type: ExternalType
    encoding: str = "utf-8"

@dataclasses.dataclass(frozen=True)
class DatetimeColumnDefinition(ExternalColumnDefinition):
    timezone: str = "UTC"


class AbstractNormalizer(ABC):
    @classmethod
    @abstractmethod
    def registry_key_family(cls) -> str:
        pass

    @classmethod
    @abstractmethod
    def registry_key(cls) -> str:
        pass

    @abstractmethod
    def normalize(self, column: e.ExpressionBuilder, dialect: e.DialectType, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        pass

class UniversalNormalizer(AbstractNormalizer, ABC):
    @classmethod
    def registry_key_family(cls) -> str:
        return "Universal"

class HandleNullsAndTrimNormalizer(UniversalNormalizer):
    @classmethod
    def registry_key(cls) -> str:
        return cls.__name__

    def normalize(self, column: e.ExpressionBuilder, dialect: e.DialectType, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return e.coalesce(e.trim(column), "__null_recon__", is_string=True)

class QuoteIdentifierNormalizer(UniversalNormalizer):
    @classmethod
    def registry_key(cls) -> str:
        return cls.__name__

    def normalize(self, column: e.ExpressionBuilder, dialect: e.DialectType, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        match dialect:
            case "oracle": return self._quote_oracle(column, column_def)
            case "databricks": return self._quote_databricks(column, column_def)
            case "snowflake": return self._quote_snowflake(column, column_def)
            case _: return column # instead of error, return as is

    def _quote_oracle(self, column: e.ExpressionBuilder, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        normalized = DialectUtils.normalize_identifier(
            column_def.column_name,
            source_start_delimiter='"',
            source_end_delimiter='"',
        ).source_normalized
        return column.column_name(normalized)

    def _quote_databricks(self, column: e.ExpressionBuilder, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        normalized = DialectUtils.ansi_quote_identifier(column_def.column_name)
        return column.column_name(normalized)

    def _quote_snowflake(self, column: e.ExpressionBuilder, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        normalized = DialectUtils.normalize_identifier(
            column_def.column_name,
            source_start_delimiter='"',
            source_end_delimiter='"',
        ).source_normalized
        return column.column_name(normalized)


class AbstractTypeNormalizer(AbstractNormalizer):
    @classmethod
    def registry_key_family(cls) -> str:
        return "ForType"

    @classmethod
    @abstractmethod
    def utype(cls) -> UType:
        pass

    def normalize(self, column: e.ExpressionBuilder, dialect: str, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        match dialect:
            case "oracle": return self._normalize_oracle(column, column_def)
            case "databricks": return self._normalize_databricks(column, column_def)
            case "snowflake": return self._normalize_snowflake(column, column_def)
            case _: return column # instead of error, return as is

    @abstractmethod
    def _normalize_oracle(self, column: e.ExpressionBuilder, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        pass

    @abstractmethod
    def _normalize_databricks(self, column: e.ExpressionBuilder, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        pass

    @abstractmethod
    def _normalize_snowflake(self, column: e.ExpressionBuilder, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        pass

class UDatetimeTypeNormalizer(AbstractTypeNormalizer):
    """
    transform all dialects to unix time
    """

    @classmethod
    def registry_key(cls) -> str:
        return cls.utype().name.name

    @classmethod
    def utype(cls) -> UType:
        return UType(ColumnTypeName.DATETIME)

    def _normalize_oracle(self, column: e.ExpressionBuilder, source_col: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return column

    def _normalize_databricks(self, column: e.ExpressionBuilder, source_col: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return e.unix_time(column)

    def _normalize_snowflake(self, column: e.ExpressionBuilder, source_col: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return column

class UStringTypeNormalizer(AbstractTypeNormalizer):

    _delegate = HandleNullsAndTrimNormalizer()

    @classmethod
    def registry_key(cls) -> str:
        return cls.utype().name.name

    @classmethod
    def utype(cls) -> UType:
        return UType(ColumnTypeName.VARCHAR)

    def _normalize_oracle(self, column: e.ExpressionBuilder,
                         column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return self._delegate.normalize(column, "", column_def)

    def _normalize_databricks(self, column: e.ExpressionBuilder,
                             column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return self._delegate.normalize(column, "", column_def)

    def _normalize_snowflake(self, column: e.ExpressionBuilder,
                            column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return self._delegate.normalize(column, "", column_def)


class NormalizersRegistry:
    _registry: dict[str,dict[str, AbstractNormalizer]] = {} # can we type this to subclass of AbstractTypeNormalizer

    def register_normalizer(self, normalizer: AbstractNormalizer): # also subclasses
        family = self._registry.get(normalizer.registry_key_family(), {})
        if family.get(normalizer.registry_key()):
            raise ValueError(f"Normalizer already registered for utype: {normalizer.registry_key_family()},{normalizer.registry_key()}")
        if not family:
            self._registry[normalizer.registry_key_family()] = {}
        self._registry[normalizer.registry_key_family()][normalizer.registry_key()] = normalizer

    def get_type_normalizer(self, name: ColumnTypeName) -> AbstractTypeNormalizer | None:
        return self._registry.get(AbstractTypeNormalizer.registry_key_family(), {}).get(name.name)

    def get_universal_normalizers(self):
        return self._registry.get(UniversalNormalizer.registry_key_family(), {}).values()

class DialectNormalizer(ABC):
    DbTypeNormalizerType = dict[ColumnTypeName, ColumnTypeName]
    # or ExternalType to UType. what about extra type information e.g scale, precision?

    dialect: e.DialectType

    @classmethod
    def type_normalizers(cls) -> DbTypeNormalizerType:
        return {
            ColumnTypeName("DATE"): UDatetimeTypeNormalizer.utype().name,
            ColumnTypeName("NCHAR"): UStringTypeNormalizer.utype().name,
            ColumnTypeName("CHAR"): UStringTypeNormalizer.utype().name,
            ColumnTypeName("VARCHAR"): UStringTypeNormalizer.utype().name,
            ColumnTypeName("NVARCHAR"): UStringTypeNormalizer.utype().name,
            ColumnTypeName("VARCHAR2"): UStringTypeNormalizer.utype().name,
        }

    def normalize(self, column_def: ExternalColumnDefinition, registry: NormalizersRegistry) -> e.ExpressionBuilder:
        start = e.ExpressionBuilder(column_def.column_name, self.dialect, None)
        for normalizer in registry.get_universal_normalizers():
            start = normalizer.normalize(start, self.dialect, column_def)
        utype = self.type_normalizers().get(column_def.data_type.name)
        if utype:
            normalizer = registry.get_type_normalizer(utype)
            if normalizer:
                return normalizer.normalize(start, self.dialect, column_def)
        return start


class OracleNormalizer(DialectNormalizer):
    dialect = "oracle"


class SnowflakeNormalizer(DialectNormalizer):
    dialect = "snowflake"

if __name__ == "__main__":
    registry = NormalizersRegistry()
    registry.register_normalizer(UDatetimeTypeNormalizer())
    registry.register_normalizer(UStringTypeNormalizer())
    # registry.register_normalizer(HandleNullsAndTrimNormalizer())
    registry.register_normalizer(QuoteIdentifierNormalizer())
    oracle = OracleNormalizer()
    snow = SnowflakeNormalizer()

    column = ExternalColumnDefinition("student_id", ExternalType(ColumnTypeName["NCHAR"]))

    oracle_column = oracle.normalize(column, registry).build()
    assert oracle_column == "COALESCE(TRIM(\"student_id\"), '__null_recon__')"
    snow_column = snow.normalize(column, registry).build()
    assert snow_column == "COALESCE(TRIM(\"student_id\"), '__null_recon__')"


