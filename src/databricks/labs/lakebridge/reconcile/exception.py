from pyspark.errors import PySparkException
from databricks.labs.lakebridge.reconcile.recon_output_config import ReconcileOutput


class ColumnMismatchException(Exception):
    """Raise the error when there is a mismatch in source and target column names"""


class DataSourceRuntimeException(Exception):
    """Raise the error when there is a runtime exception thrown in DataSource"""


class WriteToTableException(Exception):
    """Raise the error when there is a runtime exception thrown while writing data to table"""


class InvalidInputException(ValueError):
    """Raise the error when the input is invalid"""


class ReconciliationException(Exception):
    """Raise the error when there is an error occurred during reconciliation"""

    def __init__(self, message: str, reconcile_output: ReconcileOutput | None = None):
        self._reconcile_output = reconcile_output
        super().__init__(message, reconcile_output)

    @property
    def reconcile_output(self) -> ReconcileOutput | None:
        return self._reconcile_output


class ReadAndWriteWithVolumeException(PySparkException):
    """Raise the error when there is a runtime exception thrown while writing data to volume"""


class CleanFromVolumeException(PySparkException):
    """Raise the error when there is a runtime exception thrown while cleaning data from volume"""


class InvalidSnowflakePemPrivateKey(Exception):
    """Raise the error when the input private key is invalid"""
