from dataclasses import dataclass, field

from pyspark.sql import DataFrame

from databricks.labs.lakebridge.reconcile.recon_config import AggregateRule


@dataclass
class MismatchOutput:
    mismatch_df: DataFrame | None = None
    mismatch_columns: list[str] | None = None


@dataclass
class ThresholdOutput:
    threshold_df: DataFrame | None = None
    threshold_mismatch_count: int = 0


@dataclass
class DataReconcileOutput:
    mismatch_count: int = 0
    missing_in_src_count: int = 0
    missing_in_tgt_count: int = 0
    mismatch: MismatchOutput = field(default_factory=MismatchOutput)
    missing_in_src: DataFrame | None = None
    missing_in_tgt: DataFrame | None = None
    threshold_output: ThresholdOutput = field(default_factory=ThresholdOutput)
    exception: str | None = None


@dataclass
class SchemaMatchResult:
    source_column_normalized: str
    source_column_normalized_ansi: str
    source_datatype: str
    databricks_column: str
    databricks_datatype: str
    is_valid: bool = True


@dataclass
class SchemaReconcileOutput:
    is_valid: bool
    compare_df: DataFrame | None = None
    exception: str | None = None


@dataclass
class ReconcileProcessDuration:
    start_ts: str
    end_ts: str | None


@dataclass
class StatusOutput:
    row: bool | None = None
    column: bool | None = None
    schema: bool | None = None
    aggregate: bool | None = None


@dataclass
class ReconcileTableOutput:
    target_table_name: str
    source_table_name: str
    status: StatusOutput = field(default_factory=StatusOutput)
    exception_message: str | None = None


@dataclass
class ReconcileOutput:
    recon_id: str
    results: list[ReconcileTableOutput]


@dataclass
class ReconcileRecordCount:
    source: int = 0
    target: int = 0


@dataclass
class AggregateQueryOutput:
    rule: AggregateRule | None
    reconcile_output: DataReconcileOutput
