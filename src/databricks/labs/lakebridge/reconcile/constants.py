from enum import Enum, auto


class AutoName(Enum):
    """
    This class is used to auto generate the enum values based on the name of the enum in lower case

    Reference: https://docs.python.org/3/howto/enum.html#enum-advanced-tutorial
    """

    @staticmethod
    # pylint: disable-next=bad-dunder-name
    def _generate_next_value_(name, start, count, last_values):  # noqa ARG004
        return name.lower()


class ReconSourceType(AutoName):
    DATABRICKS = auto()
    MSSQL = auto()
    ORACLE = auto()
    SNOWFLAKE = auto()
    SYNAPSE = auto()


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
