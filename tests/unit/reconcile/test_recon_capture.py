from unittest.mock import MagicMock

from pyspark.sql.types import StructType, StructField, StringType, IntegerType

from databricks.labs.lakebridge.reconcile.recon_capture import ReconIntermediatePersist


def _make_df(schema_fields):
    """Create a mock DataFrame with given schema fields."""
    schema = StructType(schema_fields)
    df = MagicMock()
    df.schema = schema
    df.columns = [f.name for f in schema_fields]

    def mock_select(*cols):
        # select with alias strips metadata — return df with empty metadata on all fields
        stripped_fields = [
            StructField(f.name, f.dataType, f.nullable, metadata={})
            for f in schema_fields
        ]
        return _make_df(stripped_fields)

    df.select = mock_select
    return df


def test_strip_char_varchar_constraints_strips_metadata():
    """Column metadata should be stripped to remove CHAR/VARCHAR constraints."""
    df = _make_df([
        StructField("id", IntegerType(), False, metadata={}),
        StructField("name", StringType(), True, metadata={"__CHAR_VARCHAR_TYPE_STRING": "char(16)"}),
    ])

    result = ReconIntermediatePersist._strip_char_varchar_constraints(df)

    assert result.schema.fields[1].metadata == {}


def test_strip_char_varchar_constraints_preserves_types():
    """Column types should be preserved — only metadata is stripped."""
    df = _make_df([
        StructField("id", IntegerType(), False),
        StructField("name", StringType(), True),
    ])

    result = ReconIntermediatePersist._strip_char_varchar_constraints(df)

    assert result.schema.fields[0].dataType == IntegerType()
    assert result.schema.fields[1].dataType == StringType()



