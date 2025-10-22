#!/usr/bin/env bash
set -Eeuo pipefail

# Config
ORACLE_CONTAINER="${ORACLE_CONTAINER:-oracle-free}"
ORACLE_IMAGE="${ORACLE_IMAGE:-container-registry.oracle.com/database/free:latest-lite}"
ORACLE_PORT="${ORACLE_PORT:-1521}"
ORACLE_PWD="${ORACLE_PWD:?export ORACLE_PWD for SYS}"
ORACLE_SID="${ORACLE_SID:-FREEPDB1}"

# Dependencies
command -v docker >/dev/null || { echo "Docker not installed" >&2; exit 2; }

# Image present?
docker image inspect "${ORACLE_IMAGE}" >/dev/null 2>&1 || docker pull "${ORACLE_IMAGE}"

# Start container if needed
if docker ps --format '{{.Names}}' | grep -qx "${ORACLE_CONTAINER}"; then
  :
elif docker ps -a --format '{{.Names}}' | grep -qx "${ORACLE_CONTAINER}"; then
  docker start "${ORACLE_CONTAINER}" >/dev/null
else
  docker run --name "${ORACLE_CONTAINER}" \
    -p "${ORACLE_PORT}:1521" \
    -e ORACLE_PWD="${ORACLE_PWD}" \
    -d "${ORACLE_IMAGE}" >/dev/null
  echo "Starting Oracle container..."
fi

echo "Waiting up to 5 minutes for Oracle to be healthy..."
MAX_WAIT=300; WAIT_INTERVAL=5; waited=0
while :; do
  state="$(docker inspect -f '{{.State.Health.Status}}' "${ORACLE_CONTAINER}" 2>/dev/null || true)"
  echo "health=${state:-unknown} waited=${waited}s"
  [[ "$state" == "healthy" ]] && break
  (( waited >= MAX_WAIT )) && { echo "ERROR: Oracle not healthy in 300s" >&2; exit 1; }
  sleep "$WAIT_INTERVAL"; waited=$((waited + WAIT_INTERVAL))
done
echo "Oracle is fully started."

# SQL bootstrap as SYSDBA, target SYSTEM schema
docker exec -i "${ORACLE_CONTAINER}" bash -lc \
"sqlplus -L -s 'sys/${ORACLE_PWD}@localhost:${ORACLE_PORT}/${ORACLE_SID} as sysdba'" <<'SQL'
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET ECHO ON FEEDBACK ON PAGESIZE 200 LINESIZE 32767 SERVEROUTPUT ON

-- reconcile queries executes DBMS_CRYPTO
GRANT EXECUTE ON DBMS_CRYPTO TO SYSTEM;

-- work in SYSTEM, not SYS
ALTER SESSION SET CURRENT_SCHEMA=SYSTEM;

-- create table if not exists (guard ORA-00955)
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE SOURCE_TABLE (
      ID     NUMBER(15,0),
      DESCR  CHAR(30 CHAR),
      YEAR   NUMBER(4,0),
      DATEE  DATE,
      CONSTRAINT PK_SOURCE_TABLE PRIMARY KEY (ID)
    )
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- truncate if exists
DECLARE n INTEGER;
BEGIN
  SELECT COUNT(*) INTO n FROM USER_TABLES WHERE TABLE_NAME='SOURCE_TABLE';
  IF n=1 THEN EXECUTE IMMEDIATE 'TRUNCATE TABLE SOURCE_TABLE'; END IF;
END;
/

-- idempotent load
MERGE INTO SOURCE_TABLE t
USING (
  SELECT 1001 ID, 'Cycle 1' DESCR, 2025 YEAR, DATE '2025-01-01' DATEE FROM DUAL UNION ALL
  SELECT 1002,     'Cycle 2',       2025,      DATE '2025-02-01'       FROM DUAL UNION ALL
  SELECT 1003,     'Cycle 3',       2025,      DATE '2025-03-01'       FROM DUAL UNION ALL
  SELECT 1004,     'Cycle 4',       2025,      DATE '2025-04-15'       FROM DUAL UNION ALL
  SELECT 1005,     'Cycle 5',       2025,      DATE '2025-05-01'       FROM DUAL
) s
ON (t.ID = s.ID)
WHEN MATCHED THEN UPDATE SET t.DESCR = RPAD(s.DESCR,30), t.YEAR = s.YEAR, t.DATEE = s.DATEE
WHEN NOT MATCHED THEN INSERT (ID, DESCR, YEAR, DATEE)
                     VALUES (s.ID, RPAD(s.DESCR,30), s.YEAR, s.DATEE);

COMMIT;
SQL

echo "Oracle DDL/DML completed."
