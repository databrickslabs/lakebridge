"""Utilities for building a mock Teradata profiler DuckDB extract for testing."""

from datetime import date, datetime
from pathlib import Path

import duckdb


def build_mock_teradata_extract(db_path: Path) -> Path:
    """Create a DuckDB file with all 10 Teradata profiler tables populated with minimal realistic data."""
    with duckdb.connect(str(db_path)) as conn:
        _create_td_sys_info(conn)
        _create_td_sys_nodes_info(conn)
        _create_td_sys_usage_agg(conn)
        _create_td_sys_disk_utilization(conn)
        _create_td_db_object_types(conn)
        _create_td_user_databases(conn)
        _create_td_dwh_udf(conn)
        _create_td_pdcr_info_agg_extract(conn)
        _create_td_pdcr_sp_exe_info_agg_extract(conn)
        _create_td_dbql_core_info_extract(conn)
    return db_path


def _create_td_sys_info(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("CREATE TABLE td_sys_info (K VARCHAR, V VARCHAR)")
    conn.executemany(
        "INSERT INTO td_sys_info VALUES (?, ?)",
        [
            ("VERSION", "16.20.53.20"),
            ("RELEASE", "16.20.53.20"),
            ("LANGUAGE SUPPORT MODE", "Standard"),
            ("# of AMPs", "8"),
        ],
    )


def _create_td_sys_nodes_info(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE td_sys_nodes_info (
            NodeID BIGINT, NodeType VARCHAR, NCPUs BIGINT, Vproc1 BIGINT,
            MemSize DOUBLE, PM_COD_CPU DOUBLE, WM_COD_CPU DOUBLE,
            PM_COD_IO DOUBLE, WM_COD_IO DOUBLE, Tier_factor DOUBLE,
            NodeNormFactor DOUBLE
        )
    """)
    conn.execute(
        "INSERT INTO td_sys_nodes_info VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [10001, "000CLV", 4, 4, 11264.0, 4.0, 4.0, 0.0, 0.0, 1.0, 4505.0],
    )


def _create_td_sys_usage_agg(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE td_sys_usage_agg (
            TheDate DATE, hour_of_day BIGINT, totNCPUs DOUBLE, totVproc1 DOUBLE,
            totCPUUExec DOUBLE, totCPUUServ DOUBLE, totCPUIoWait DOUBLE,
            totMemSizeMB DOUBLE, totCPUIdle DOUBLE, totMemFreeMB DOUBLE
        )
    """)
    conn.executemany(
        "INSERT INTO td_sys_usage_agg VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (date(2025, 3, 1), 10, 4.0, 4.0, 120.5, 30.2, 5.1, 11264.0, 3200.0, 4096.0),
            (date(2025, 3, 1), 11, 4.0, 4.0, 150.0, 40.0, 8.0, 11264.0, 2800.0, 3800.0),
        ],
    )


def _create_td_sys_disk_utilization(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE td_sys_disk_utilization (
            DATABASENAME VARCHAR, MAX_PERM_MB DOUBLE, CURRENT_PERM_MB DOUBLE,
            MAX_SPOOL_MB DOUBLE, CURRENT_SPOOL_MB DOUBLE
        )
    """)
    conn.executemany(
        "INSERT INTO td_sys_disk_utilization VALUES (?, ?, ?, ?, ?)",
        [
            ("analytics_db", 102400.0, 82000.0, 20480.0, 5120.0),
            ("staging_db", 51200.0, 12800.0, 10240.0, 1024.0),
        ],
    )


def _create_td_db_object_types(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE td_db_object_types (
            DatabaseName VARCHAR, TableKind VARCHAR, TableKindCount BIGINT
        )
    """)
    conn.executemany(
        "INSERT INTO td_db_object_types VALUES (?, ?, ?)",
        [
            ("analytics_db", "T", 42),
            ("analytics_db", "V", 15),
            ("analytics_db", "P", 3),
            ("staging_db", "T", 10),
            ("staging_db", "O", 2),
        ],
    )


def _create_td_user_databases(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE td_user_databases (
            DatabaseName VARCHAR, CreatorName VARCHAR,
            CreateTimeStamp TIMESTAMP, LastAlterTimeStamp TIMESTAMP,
            ProtectionType VARCHAR, JournalFlag VARCHAR,
            PermSpace BIGINT, SpoolSpace BIGINT, TempSpace BIGINT
        )
    """)
    conn.executemany(
        "INSERT INTO td_user_databases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "analytics_db",
                "DBC",
                datetime(2020, 1, 15),
                datetime(2024, 6, 1),
                "F",
                "NN",
                107374182400,
                21474836480,
                21474836480,
            ),
            (
                "staging_db",
                "DBC",
                datetime(2021, 3, 10),
                datetime(2024, 8, 20),
                "F",
                "NN",
                53687091200,
                10737418240,
                10737418240,
            ),
        ],
    )


def _create_td_dwh_udf(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE td_dwh_udf (
            DatabaseName VARCHAR, FunctionName VARCHAR,
            NumParameters BIGINT, ParameterDataTypes VARCHAR,
            FunctionLanguage VARCHAR, FunctionType VARCHAR,
            ReturnType VARCHAR
        )
    """)
    conn.executemany(
        "INSERT INTO td_dwh_udf VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (None, "ONNXPREDICT", 0, None, "JAVA", "Table operator", None),
            ("mldb", "ONNXPREDICT_CONTRACT", 0, None, "JAVA", "Contract function", "I"),
            ("GLOBAL_FUNCTIONS", "BYTE2INT", 1, "BF", "C", "Scalar", "I"),
        ],
    )


def _create_td_pdcr_info_agg_extract(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE td_pdcr_info_agg_extract (
            LogType VARCHAR, LogDate DATE, DOW VARCHAR, HR BIGINT,
            LogHour_TS TIMESTAMP, Organization VARCHAR, Department VARCHAR,
            WdName VARCHAR, AcctString VARCHAR, Username VARCHAR,
            userId VARCHAR, AppId VARCHAR, QueryType VARCHAR,
            QryCNT BIGINT, SumCPU DOUBLE, AvgCPU DOUBLE, MaxCPU DOUBLE,
            SumIO DOUBLE, AvgIO DOUBLE, MaxIO DOUBLE,
            MaxTDWMDelayTime DOUBLE, SumTDWMDelayTime DOUBLE,
            AvgRespSecs DOUBLE, MaxRespSecs DOUBLE
        )
    """)
    conn.execute(
        "INSERT INTO td_pdcr_info_agg_extract VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "DBQL",
            date(2025, 3, 1),
            "Sat",
            10,
            datetime(2025, 3, 1, 10, 0),
            "IT",
            "Analytics",
            "WD_DEFAULT",
            "$M$$$",
            "DEMO_USER",
            "DEMO_USER",
            "BTEQ",
            "Select",
            85,
            4.79,
            0.056,
            1.2,
            10127.0,
            119.1,
            500.0,
            0.0,
            0.0,
            0.5,
            3.2,
        ],
    )


def _create_td_pdcr_sp_exe_info_agg_extract(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE td_pdcr_sp_exe_info_agg_extract (
            ProcName VARCHAR, avgAMPCPUTime DOUBLE, avgExecutionSecs DOUBLE,
            NumStatements DOUBLE, FirstExeDate DATE, LastExeDate DATE,
            NumExecutions BIGINT
        )
    """)
    conn.execute(
        "INSERT INTO td_pdcr_sp_exe_info_agg_extract VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["analytics_db.sp_daily_load", 2.5, 15.3, 8.0, date(2025, 1, 1), date(2025, 3, 1), 60],
    )


def _create_td_dbql_core_info_extract(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE td_dbql_core_info_extract (
            AppID VARCHAR, UserName VARCHAR, SessionID BIGINT,
            SQLTextInfo VARCHAR, StartTime TIMESTAMP, FirstRespTime TIMESTAMP,
            TotalFirstRespTime DOUBLE, TotalCPUTime DOUBLE,
            TotalIOCount DOUBLE, ReqPhysIOKB DOUBLE, SpoolUsage DOUBLE
        )
    """)
    conn.executemany(
        "INSERT INTO td_dbql_core_info_extract VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "BTEQ",
                "DEMO_USER",
                1001,
                "SELECT * FROM analytics_db.fact_sales",
                datetime(2025, 3, 1, 10, 0),
                datetime(2025, 3, 1, 10, 0, 1),
                0.8,
                1.2,
                5000.0,
                1024.0,
                7340032.0,
            ),
            (
                "BTEQ",
                "DEMO_USER",
                1002,
                "SELECT COUNT(*) FROM staging_db.raw_events",
                datetime(2025, 3, 1, 10, 5),
                datetime(2025, 3, 1, 10, 5, 0),
                0.3,
                0.4,
                200.0,
                128.0,
                0.0,
            ),
        ],
    )
