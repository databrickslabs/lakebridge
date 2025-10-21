#!/bin/bash

set -xve

# Set Oracle container credentials
ORACLE_CONTAINER="oracle-free"
ORACLE_IMAGE="container-registry.oracle.com/database/free:latest-lite"
ORACLE_PORT="1521"
ORACLE_PWD="FIXME"
ORACLE_SID="FREEPDB1"

# Start Oracle container (if not running already)
if ! docker ps | grep -q $ORACLE_CONTAINER; then
  docker run --name $ORACLE_CONTAINER -p $ORACLE_PORT:1521 -e ORACLE_PWD=$ORACLE_PWD -d $ORACLE_IMAGE
  echo "Starting Oracle container. Waiting for initialization (may take several minutes)..."
fi

echo "Waiting up to 5 minutes for Oracle to be ready..."

MAX_WAIT=300
WAIT_INTERVAL=5
TIME_WAITED=0

until [ "$(docker inspect -f "{{.State.Health.Status}}" $ORACLE_CONTAINER)" = "healthy" ]; do
  sleep $WAIT_INTERVAL
  TIME_WAITED=$((TIME_WAITED + WAIT_INTERVAL))
  if [ "$TIME_WAITED" -ge "$MAX_WAIT" ]; then
    echo "ERROR: Oracle did not start after 5 minutes."
    exit 1
  fi
done

echo "Oracle is fully started!"

# Prepare Oracle SQL statements
SQL_COMMANDS="

CREATE TABLE IF NOT EXISTS source_table (
    id      NUMBER(15,0),
    descr   CHAR(30 CHAR),
    Year    NUMBER(4,0),
    datee   DATE
);

TRUNCATE TABLE source_table;

INSERT INTO source_table(id, descr, Year, datee) VALUES
    (1001, 'Cycle 1', 2025, TO_DATE('2025-01-01','YYYY-MM-DD')),
    (1002, 'Cycle 2', 2025, TO_DATE('2025-02-01','YYYY-MM-DD')),
    (1003, 'Cycle 3', 2025, TO_DATE('2025-03-01','YYYY-MM-DD')),
    (1004, 'Cycle 4', 2025, TO_DATE('2025-04-15','YYYY-MM-DD')),
    (1005, 'Cycle 5', 2025, TO_DATE('2025-05-01','YYYY-MM-DD'));
"

docker exec -i oracle-free bash -c "sqlplus 'sys/$ORACLE_PWD@localhost:$ORACLE_PORT/$ORACLE_SID as sysdba'" <<< "$SQL_COMMANDS"

echo "Oracle table creation and insert operations completed."
