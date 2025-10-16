import dataclasses
from abc import ABC, abstractmethod

import expressions as e
from databricks.labs.lakebridge.reconcile.connectors.dialect_utils import DialectUtils
from utypes import ExternalType, UType, DatabaseTypeName


@dataclasses.dataclass(frozen=True)
class ExternalColumnDefinition:
    name: str
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
        return cls.__name__

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
            case "oracle": return self.normalize_oracle(column, column_def)
            case "databricks": return self.normalize_databricks(column, column_def)
            case "snowflake": return self.normalize_snowflake(column, column_def)
            case _: return column # instead of error, return as is

    def normalize_oracle(self, column: e.ExpressionBuilder, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        normalized = DialectUtils.normalize_identifier(
            column_def.name,
            source_start_delimiter='"',
            source_end_delimiter='"',
        ).source_normalized
        return column.column_name(normalized)

    def normalize_databricks(self, column: e.ExpressionBuilder, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        normalized = DialectUtils.ansi_normalize_identifier(column_def.name)
        return column.column_name(normalized)

    def normalize_snowflake(self, column: e.ExpressionBuilder, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        normalized = DialectUtils.normalize_identifier(
            column_def.name,
            source_start_delimiter='"',
            source_end_delimiter='"',
        ).source_normalized
        return column.column_name(normalized)


class AbstractTypeNormalizer(AbstractNormalizer):
    @classmethod
    def registry_key_family(cls) -> str:
        return cls.__name__

    @classmethod
    @abstractmethod
    def utype(cls) -> UType:
        pass

    def normalize(self, column: e.ExpressionBuilder, dialect: str, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        match dialect:
            case "oracle": return self.normalize_oracle(column, column_def)
            case "databricks": return self.normalize_databricks(column, column_def)
            case "snowflake": return self.normalize_snowflake(column, column_def)
            case _: return column # instead of error, return as is

    @abstractmethod
    def normalize_oracle(self, column: e.ExpressionBuilder, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        pass

    @abstractmethod
    def normalize_databricks(self, column: e.ExpressionBuilder, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        pass

    @abstractmethod
    def normalize_snowflake(self, column: e.ExpressionBuilder, column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
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
        return UType(DatabaseTypeName.DATETIME)

    def normalize_oracle(self, column: e.ExpressionBuilder, source_col: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return column

    def normalize_databricks(self, column: e.ExpressionBuilder, source_col: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return e.unix_time(column)

    def normalize_snowflake(self, column: e.ExpressionBuilder, source_col: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return column

class UStringTypeNormalizer(AbstractTypeNormalizer):

    _delegate = HandleNullsAndTrimNormalizer()

    @classmethod
    def registry_key(cls) -> str:
        return cls.utype().name.name

    @classmethod
    def utype(cls) -> UType:
        return UType(cls.__name__.removesuffix("TypeNormalizer").upper())

    def normalize_oracle(self, column: e.ExpressionBuilder,
                         column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return self._delegate.normalize(column, "", column_def)

    def normalize_databricks(self, column: e.ExpressionBuilder,
                             column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return self._delegate.normalize(column, "", column_def)

    def normalize_snowflake(self, column: e.ExpressionBuilder,
                            column_def: ExternalColumnDefinition) -> e.ExpressionBuilder:
        return self._delegate.normalize(column, "", column_def)


class NormalizersRegistry:
    registry: dict[str,dict[str, AbstractNormalizer]] = {} # can we type this to subclass of AbstractTypeNormalizer

    def register_normalizer(self, normalizer: AbstractNormalizer): # also subclasses
        family = self.registry.get(normalizer.registry_key_family(), {})
        if family.get(normalizer.registry_key()):
            raise ValueError(f"Normalizer already registered for utype: {normalizer.registry_key_family()},{normalizer.registry_key()}")
        if not family:
            self.registry[normalizer.registry_key_family()] = {}
        self.registry[normalizer.registry_key_family()][normalizer.registry_key()] = normalizer

    def get_type_normalizer(self, name: DatabaseTypeName) -> AbstractTypeNormalizer | None:
        return self.registry.get(AbstractTypeNormalizer.registry_key_family()).get(name.name)

class DialectNormalizer(ABC):
    DbTypeNormalizerType = dict[DatabaseTypeName, DatabaseTypeName]
    # or ExternalType to UType. what about extra type information e.g scale, precision?

    dialect: e.DialectType

    @classmethod
    def type_normalizers(cls, overrides: DbTypeNormalizerType) -> DbTypeNormalizerType:
        return {
            DatabaseTypeName("DATE"): UDatetimeTypeNormalizer.utype().name,
            **overrides
        }

    @abstractmethod
    def normalize(self, column_def: ExternalColumnDefinition, registry: NormalizersRegistry) -> e.ExpressionBuilder:
        pass


class OracleNormalizer(DialectNormalizer):
    dialect = "oracle"

    def normalize(self, column_def: ExternalColumnDefinition, registry: NormalizersRegistry) -> e.ExpressionBuilder:
        start = e.ExpressionBuilder(column_def.name, self.dialect, None)
        utype = self.type_normalizers({}).get(column_def.data_type.name)
        if utype:
            normalizer = registry.get_type_normalizer(utype)
            if normalizer:
                return normalizer.normalize_oracle(start, column_def)
        return start

if __name__ == "__main__":
    registry = NormalizersRegistry()
    registry.register_normalizer(UDatetimeTypeNormalizer())
    oracle = OracleNormalizer()

    column = ExternalColumnDefinition("student_id", ExternalType(DatabaseTypeName["NCHAR"]))
    column_builder = oracle.normalize(column, registry)

    sql = column_builder.build()
    print(sql)
    assert sql == "SELECT student_id"
