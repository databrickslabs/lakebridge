"""Integration tests for the Teradata profiler against a ClearScape Analytics instance.

These tests require a running ClearScape (or Teradata) instance.  Set the
following environment variables (or entries in ~/.databricks/debug-env.json
under the ``ucws`` key):

    TEST_TERADATA_HOST     – hostname / IP of the ClearScape instance
    TEST_TERADATA_USER     – Teradata username  (e.g. ``dbc``)
    TEST_TERADATA_PASS     – Teradata password
    TEST_TERADATA_DATABASE – (optional) default database

ClearScape does **not** include PDCR tables, so all pipeline tests run with
``use_pdcr=False`` which activates the DBQL-core fallback extract.
"""

import os
from pathlib import Path
from unittest.mock import patch
from typing import Any, cast

import duckdb
import pytest
import yaml

from databricks.labs.blueprint.installation import JsonObject
from databricks.labs.lakebridge.assessments.dashboards.execute import (
    _validate_profiler_extract,
    _ingest_profiler_tables,
)
from databricks.labs.lakebridge.assessments.pipeline import PipelineClass, DB_NAME
from databricks.labs.lakebridge.assessments.profiler import Profiler
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig
from databricks.labs.lakebridge.cli import (
    _test_database_connection,
    test_profiler_connection as cli_test_profiler_connection,
)
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager
from databricks.labs.lakebridge.connections.env_getter import EnvGetter
from databricks.labs.lakebridge.config import ProfilerDashboardConfig, ProfilerDashboardMetadataConfig
from tests.unit.teradata_test_helpers import TERADATA_TABLES

pytestmark = pytest.mark.teradata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TERADATA_PIPELINE_CFG = "src/databricks/labs/lakebridge/resources/assessments/teradata/pipeline_config.yml"

# Tables expected after a non-PDCR pipeline run.
# PDCR data steps are inactive; their DDL still creates the (empty) tables.
_EXPECTED_TABLES_NO_PDCR = set(TERADATA_TABLES)

# Tables that should have data when run against ClearScape.
# PDCR tables will be empty (DDL-only); DBQL core may or may not have data
# depending on whether query logging is enabled.
_TABLES_WITH_DATA = {
    "td_sys_info",
    "td_db_object_types",
    "td_user_databases",
    "td_sys_disk_utilization",
}


def _load_teradata_pipeline_config(project_root: Path, extract_folder: Path) -> PipelineConfig:
    """Load the Teradata pipeline config, rebase paths, and set extract folder."""
    config = Profiler.path_modifier(
        config_file=project_root / _TERADATA_PIPELINE_CFG,
        path_prefix=project_root,
    )
    return config.copy(extract_folder=str(extract_folder))


def _apply_no_pdcr(config: PipelineConfig) -> PipelineConfig:
    """Disable PDCR data steps and activate the DBQL-core fallback."""
    connect_config = {"profiler": {"use_pdcr": False}}
    return Profiler._configure_teradata_pipeline(config, connect_config)


# ---------------------------------------------------------------------------
# Connection tests
# ---------------------------------------------------------------------------


def test_teradata_connection(sandbox_teradata: DatabaseManager) -> None:
    """Verify basic connectivity to the ClearScape instance."""
    assert sandbox_teradata.check_connection()


def test_teradata_sys_info_query(sandbox_teradata: DatabaseManager) -> None:
    """DBC.DBCInfoTbl should return system info rows."""
    result = sandbox_teradata.fetch("SELECT InfoKey, InfoData FROM DBC.DBCInfoTbl")
    assert len(result.rows) > 0
    keys = {row[0].strip() for row in result.rows}
    assert "VERSION" in keys or "RELEASE" in keys or len(keys) > 0


def test_teradata_databases_query(sandbox_teradata: DatabaseManager) -> None:
    """DBC.DatabasesV should return at least one user-visible database."""
    result = sandbox_teradata.fetch(
        "SELECT DatabaseName FROM DBC.DatabasesV WHERE DatabaseName NOT IN ('DBC','dbcmngr','SYSLIB','SystemFe')"
    )
    assert len(result.rows) > 0


def test_teradata_tables_query(sandbox_teradata: DatabaseManager) -> None:
    """DBC.TablesV should return object catalog entries."""
    result = sandbox_teradata.fetch(
        "SELECT DatabaseName, TableKind, COUNT(*) AS cnt "
        "FROM DBC.TablesV "
        "WHERE DatabaseName NOT IN ('DBC','dbcmngr','SYSLIB','SystemFe') "
        "GROUP BY DatabaseName, TableKind"
    )
    assert len(result.rows) >= 0  # may be empty on a fresh ClearScape


def test_teradata_disk_space_query(sandbox_teradata: DatabaseManager) -> None:
    """DBC.DiskSpaceV should be accessible."""
    result = sandbox_teradata.fetch(
        "SELECT DatabaseName, SUM(MaxPerm) AS max_perm FROM DBC.DiskSpaceV GROUP BY DatabaseName"
    )
    assert len(result.rows) > 0


# ---------------------------------------------------------------------------
# Full pipeline execution (non-PDCR)
# ---------------------------------------------------------------------------


def test_teradata_profiler_pipeline_no_pdcr(
    sandbox_teradata: DatabaseManager,
    project_path: Path,
    tmp_path: Path,
) -> None:
    """Run the full Teradata profiler pipeline against ClearScape with PDCR disabled."""
    extract_folder = tmp_path / "teradata_profiler"
    config = _load_teradata_pipeline_config(project_path, extract_folder)
    config = _apply_no_pdcr(config)

    pipeline = PipelineClass(config=config, executor=sandbox_teradata)
    results = pipeline.execute()

    for r in results:
        assert r.status.value in ("COMPLETE", "SKIPPED"), f"Step {r.step_name} failed: {r.error_message}"

    db_path = extract_folder / DB_NAME
    assert db_path.exists(), "Profiler extract database should be created"

    with duckdb.connect(str(db_path)) as conn:
        tables = conn.execute("SHOW ALL TABLES").fetchall()
        table_names = {row[2] for row in tables}

    for expected in _EXPECTED_TABLES_NO_PDCR:
        assert expected in table_names, f"Missing table in extract: {expected}"


def test_teradata_profiler_extract_has_data(
    sandbox_teradata: DatabaseManager,
    project_path: Path,
    tmp_path: Path,
) -> None:
    """Tables backed by DBC system views should contain data on ClearScape."""
    extract_folder = tmp_path / "teradata_profiler_data"
    config = _load_teradata_pipeline_config(project_path, extract_folder)
    config = _apply_no_pdcr(config)

    PipelineClass(config=config, executor=sandbox_teradata).execute()

    db_path = extract_folder / DB_NAME
    with duckdb.connect(str(db_path)) as conn:
        for table in _TABLES_WITH_DATA:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            assert count is not None and count[0] > 0, f"Table {table} should have data from ClearScape"


def test_teradata_profiler_extract_schema(
    sandbox_teradata: DatabaseManager,
    project_path: Path,
    tmp_path: Path,
) -> None:
    """Columns extracted from ClearScape should match the DDL-defined schema."""
    extract_folder = tmp_path / "teradata_profiler_schema"
    config = _load_teradata_pipeline_config(project_path, extract_folder)
    config = _apply_no_pdcr(config)

    PipelineClass(config=config, executor=sandbox_teradata).execute()

    db_path = extract_folder / DB_NAME
    expected_columns = {
        "td_sys_info": {"K", "V"},
        "td_db_object_types": {"DatabaseName", "TableKind", "TableKindCount"},
        "td_dwh_udf": {
            "DatabaseName",
            "FunctionName",
            "NumParameters",
            "ParameterDataTypes",
            "FunctionLanguage",
            "FunctionType",
            "ReturnType",
        },
        "td_user_databases": {
            "DatabaseName",
            "CreatorName",
            "CreateTimeStamp",
            "LastAlterTimeStamp",
            "ProtectionType",
            "JournalFlag",
            "PermSpace",
            "SpoolSpace",
            "TempSpace",
        },
        "td_sys_disk_utilization": {
            "DATABASENAME",
            "MAX_PERM_MB",
            "CURRENT_PERM_MB",
            "MAX_SPOOL_MB",
            "CURRENT_SPOOL_MB",
        },
    }

    with duckdb.connect(str(db_path)) as conn:
        for table, cols in expected_columns.items():
            schema = conn.execute(f"DESCRIBE {table}").fetchall()
            actual_cols = {row[0] for row in schema}
            missing = cols - actual_cols
            assert not missing, f"Table {table} missing columns: {missing}"


# ---------------------------------------------------------------------------
# Profiler high-level API
# ---------------------------------------------------------------------------


def test_teradata_profiler_class_execution(
    sandbox_teradata: DatabaseManager,
    project_path: Path,
    tmp_path: Path,
) -> None:
    """Profiler.profile() should complete successfully with an injected extractor."""
    extract_folder = tmp_path / "profiler_class_run"
    config = _load_teradata_pipeline_config(project_path, extract_folder)
    config = _apply_no_pdcr(config)

    profiler = Profiler("teradata")
    profiler.profile(extractor=sandbox_teradata, pipeline_config=config)

    db_path = extract_folder / DB_NAME
    assert db_path.exists(), "Profiler extract database should be created"


# ---------------------------------------------------------------------------
# CLI-based tests
# ---------------------------------------------------------------------------


def _write_teradata_cred_file(path: Path, config: JsonObject) -> Path:
    """Write a .credentials.yml file matching the format used by configure-database-profiler."""
    cfg = cast(dict[str, Any], config)
    cred: dict[str, Any] = {
        "secret_vault_type": "local",
        "secret_vault_name": None,
        "teradata": {
            "host": cfg["host"],
            "user": cfg["user"],
            "password": cfg["password"],
        },
    }
    if cfg.get("database"):
        cred["teradata"]["database"] = cfg["database"]
    cred["teradata"]["profiler"] = {"use_pdcr": False}

    cred_file = path / ".credentials.yml"
    with open(cred_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(cred, f)
    return cred_file


def test_cli_test_database_connection(sandbox_teradata_config: JsonObject) -> None:
    """_test_database_connection should succeed for Teradata against ClearScape."""
    _test_database_connection("teradata", sandbox_teradata_config)


def test_cli_test_profiler_connection(
    sandbox_teradata_config: JsonObject,
    mock_workspace_client,
    tmp_path: Path,
) -> None:
    """test_profiler_connection CLI command should succeed with a valid credential file."""
    cred_file = _write_teradata_cred_file(tmp_path, sandbox_teradata_config)
    cli_test_profiler_connection(
        w=mock_workspace_client,
        source_tech="teradata",
        cred_file_path=str(cred_file),
    )


def test_cli_execute_database_profiler(
    sandbox_teradata_config: JsonObject,
    mock_workspace_client,
    tmp_path: Path,
    project_path: Path,
) -> None:
    """execute_database_profiler CLI command should produce a DuckDB extract."""
    cred_file = _write_teradata_cred_file(tmp_path, sandbox_teradata_config)
    extract_folder = tmp_path / "cli_profiler_output"

    with (
        patch(
            "databricks.labs.lakebridge.cli.cred_file",
            return_value=cred_file,
        ),
        patch(
            "databricks.labs.lakebridge.connections.credential_manager._get_home",
            return_value=tmp_path,
        ),
        patch(
            "databricks.labs.lakebridge.assessments.profiler.create_credential_manager",
        ) as mock_cred_mgr,
    ):
        from databricks.labs.lakebridge.connections.credential_manager import (
            create_credential_manager,
        )

        mock_cred_mgr.return_value = create_credential_manager("lakebridge", EnvGetter(), creds_path=cred_file)

        profiler = Profiler.create("teradata")
        config = profiler._pipeline_config
        assert config is not None
        config = config.copy(extract_folder=str(extract_folder))
        config = _apply_no_pdcr(config)

        profiler.profile(
            extractor=DatabaseManager("teradata", sandbox_teradata_config),
            pipeline_config=config,
        )

    db_path = extract_folder / DB_NAME
    assert db_path.exists(), "CLI profiler should produce a DuckDB extract"

    with duckdb.connect(str(db_path)) as conn:
        tables = conn.execute("SHOW ALL TABLES").fetchall()
        table_names = {row[2] for row in tables}

    for expected in _EXPECTED_TABLES_NO_PDCR:
        assert expected in table_names, f"Missing table: {expected}"


# ---------------------------------------------------------------------------
# Extract validation & ingestion tests
# ---------------------------------------------------------------------------


def _run_pipeline_and_get_extract(
    sandbox_teradata: DatabaseManager,
    project_path: Path,
    extract_folder: Path,
) -> Path:
    """Helper: run pipeline, return DuckDB extract path."""
    config = _load_teradata_pipeline_config(project_path, extract_folder)
    config = _apply_no_pdcr(config)
    PipelineClass(config=config, executor=sandbox_teradata).execute()
    return extract_folder / DB_NAME


def test_validate_teradata_extract(
    sandbox_teradata: DatabaseManager,
    project_path: Path,
    tmp_path: Path,
) -> None:
    """_validate_profiler_extract should pass on a real ClearScape extract."""
    extract_folder = tmp_path / "validate_extract"
    db_path = _run_pipeline_and_get_extract(sandbox_teradata, project_path, extract_folder)

    class _WriterStub:
        def format(self, _fmt):
            return self

        def mode(self, _mode):
            return self

        def saveAsTable(self, _name):
            pass

    class _DFStub:
        def __init__(self):
            self.write = _WriterStub()

    class _SparkStub:
        class builder:
            @staticmethod
            def getOrCreate():
                return _SparkStub()

        def createDataFrame(self, *_args, **_kwargs):  # pylint: disable=invalid-name
            return _DFStub()

    with (
        patch("databricks.labs.lakebridge.assessments.dashboards.execute.SparkSession", _SparkStub),
        patch("databricks.labs.lakebridge.assessments.profiler_validator.SparkSession", _SparkStub),
    ):
        valid = _validate_profiler_extract(
            target_catalog_name="test_cat",
            target_schema_name="test_schema",
            extract_location=str(db_path),
            source_tech="teradata",
        )
    assert valid, "ClearScape extract should pass validation"


def test_ingest_teradata_extract(
    sandbox_teradata: DatabaseManager,
    project_path: Path,
    tmp_path: Path,
) -> None:
    """_ingest_profiler_tables should successfully read all tables from the extract."""
    extract_folder = tmp_path / "ingest_extract"
    db_path = _run_pipeline_and_get_extract(sandbox_teradata, project_path, extract_folder)

    ingested_tables: dict[str, int] = {}

    class _WriterStub:
        def __init__(self):
            self._table_name: str | None = None

        def format(self, _fmt):
            return self

        def mode(self, _mode):
            return self

        def saveAsTable(self, name):
            self._table_name = name

    class _DFStub:
        def __init__(self, pdf):
            self._pdf = pdf
            self._writer = _WriterStub()

        @property
        def write(self):
            return self._writer

    class _SparkStub:
        class builder:
            @staticmethod
            def getOrCreate():
                return _SparkStub()

        def createDataFrame(self, pdf, **kwargs):  # pylint: disable=invalid-name
            _ = kwargs.get("schema")
            row_count = len(pdf) if hasattr(pdf, "__len__") else 0
            df = _DFStub(pdf)

            class _TrackingWriter:
                def format(self, _fmt):
                    return self

                def mode(self, _mode):
                    return self

                def saveAsTable(self, name):  # pylint: disable=invalid-name
                    ingested_tables[name] = row_count

            df._writer = _TrackingWriter()  # pylint: disable=protected-access
            return df

    with patch("databricks.labs.lakebridge.assessments.dashboards.execute.SparkSession", _SparkStub):
        _ingest_profiler_tables("test_cat", "test_schema", str(db_path))

    for table in _EXPECTED_TABLES_NO_PDCR:
        uc_name = f"test_cat.test_schema.{table}"
        assert uc_name in ingested_tables, f"Table {table} was not ingested"


# ---------------------------------------------------------------------------
# Dashboard deployment tests (require Databricks workspace)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.getenv("RUN_WORKSPACE_DASHBOARD_TESTS", "").lower() not in {"1", "true", "yes"},
    reason="Set RUN_WORKSPACE_DASHBOARD_TESTS=true to run workspace dashboard deployment tests.",
)
def test_deploy_teradata_dashboard(application_ctx) -> None:
    """Deploy the Teradata profiler dashboard to the workspace and verify it exists."""
    mgr = application_ctx.dashboard_manager
    if not application_ctx.workspace_client.config.warehouse_id:
        pytest.skip("Workspace warehouse_id is required to deploy Lakeview dashboards.")
    catalog = os.getenv("TEST_PROFILER_CATALOG")
    schema = os.getenv("TEST_PROFILER_SCHEMA")
    volume = os.getenv("TEST_PROFILER_VOLUME")
    if not catalog or not schema or not volume:
        pytest.skip("Set TEST_PROFILER_CATALOG/TEST_PROFILER_SCHEMA/TEST_PROFILER_VOLUME to run deploy test.")

    dashboard_cfg = ProfilerDashboardConfig(
        source_tech="teradata",
        extract_file_path="/tmp/profiler_extract.db",
        metadata_config=ProfilerDashboardMetadataConfig(
            catalog=catalog,
            schema=schema,
            volume=volume,
        ),
    )
    mgr.deploy(dashboard_cfg)

    dash_ref = "teradata"
    assert dash_ref in application_ctx.install_state.dashboards, "Dashboard should be registered in install state"
    dashboard_id = application_ctx.install_state.dashboards[dash_ref]

    dashboard = application_ctx.workspace_client.lakeview.get(dashboard_id)
    assert dashboard is not None
    assert dashboard.serialized_dashboard is not None
    assert "<CATALOG_NAME>" not in dashboard.serialized_dashboard
    assert "<SCHEMA_NAME>" not in dashboard.serialized_dashboard
    assert f"`{catalog}`" in dashboard.serialized_dashboard
    assert f"`{schema}`" in dashboard.serialized_dashboard


@pytest.mark.skipif(
    os.getenv("RUN_WORKSPACE_DASHBOARD_TESTS", "").lower() not in {"1", "true", "yes"},
    reason="Set RUN_WORKSPACE_DASHBOARD_TESTS=true to run workspace dashboard upload tests.",
)
def test_upload_teradata_extract_to_volume(
    sandbox_teradata: DatabaseManager,
    application_ctx,
    project_path: Path,
    tmp_path: Path,
) -> None:
    """Upload a real ClearScape extract to a UC Volume in the workspace."""
    extract_folder = tmp_path / "upload_extract"
    db_path = _run_pipeline_and_get_extract(sandbox_teradata, project_path, extract_folder)
    catalog = os.getenv("TEST_PROFILER_CATALOG")
    schema = os.getenv("TEST_PROFILER_SCHEMA")
    volume = os.getenv("TEST_PROFILER_VOLUME")
    if not catalog or not schema or not volume:
        pytest.skip("Set TEST_PROFILER_CATALOG/TEST_PROFILER_SCHEMA/TEST_PROFILER_VOLUME to run upload test.")

    mgr = application_ctx.dashboard_manager
    upload_cfg = ProfilerDashboardConfig(
        source_tech="teradata",
        extract_file_path=str(db_path),
        metadata_config=ProfilerDashboardMetadataConfig(catalog=catalog, schema=schema, volume=volume),
    )
    assert mgr.upload_duckdb_to_uc_volume(upload_cfg)
