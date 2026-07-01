from enum import Enum, auto

# Default number of sample rows captured per result set (mismatch / missing-in-source /
# missing-in-target) when sampling_options is not configured. Single source of truth.
DEFAULT_SAMPLE_ROWS = 50

# Prefix for the per-call temp views registered by the Databricks sampling path (created in
# SamplingQueryBuilder._recon_subquery_from_temp_view). Dropped once per run in
# TriggerReconService.trigger_recon. Single source of truth.
RECON_SAMPLE_VIEW_PREFIX = "recon_keys_"


class AutoName(Enum):
    """
    This class is used to auto generate the enum values based on the name of the enum in lower case

    Reference: https://docs.python.org/3/howto/enum.html#enum-advanced-tutorial
    """

    @staticmethod
    def _generate_next_value_(name, start, count, last_values):  # noqa ARG004
        return name.lower()


class ReconSourceType(AutoName):
    DATABRICKS = auto()
    MSSQL = auto()
    ORACLE = auto()
    SNOWFLAKE = auto()
    SYNAPSE = auto()
    REDSHIFT = auto()
    TERADATA = auto()


class ReconReportType(AutoName):
    DATA = auto()
    SCHEMA = auto()
    ROW = auto()
    ALL = auto()


class SamplingSpecificationsType(AutoName):
    FRACTION = auto()
    COUNT = auto()


class SamplingOptionMethod(AutoName):
    RANDOM = auto()
    STRATIFIED = auto()
