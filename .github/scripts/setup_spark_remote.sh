#!/bin/bash
#
# TODO: Replace this script with a GHA service container on the workflow.
#
# Set up Apache Spark in local Connect mode for integration tests.
#
# All downloads are pinned to exact versions and verified against embedded
# cryptographic checksums before use.
#
set -eu

mkdir -p "$HOME"/spark
cd "$HOME"/spark || exit 1

# Spark Connect server still points to 3.5.5
spark_version="3.5.5"
spark="spark-${spark_version}-bin-hadoop3"
spark_connect="spark-connect_2.12"
spark_sha256='8daa3f7fb0af2670fe11beb8a2ac79d908a534d7298353ec4746025b102d5e31'

mssql_jdbc_version="1.4.0"
mssql_jdbc="spark-mssql-connector_2.12-${mssql_jdbc_version}-BETA"
mssql_jdbc_sha256="1057e93d946dffd2ecac1c11bcc40fdf51110c5a99e9e7379a9568584cc3de7f"
ORACLE_JDBC_VERSION="19.28.0.0"
SNOWFLAKE_JDBC_VERSION="3.26.1"
SNOWFLAKE_SPARK_VERSION="2.11.2-spark_3.3"

mkdir -p "${spark}"
SERVER_SCRIPT=$HOME/spark/${spark}/sbin/start-connect-server.sh
JARS_DIR=$HOME/spark/${spark}/jars

if [ -f "${SERVER_SCRIPT}" ]; then
  printf "Spark %s already exists\n" "${spark_version}"
else
  spark_tarball="${spark}.tgz"
  if [ ! -f "${spark_tarball}" ]; then
    printf "Downloading Spark %s...\n" "${spark_version}"
    curl -fsSL "https://archive.apache.org/dist/spark/spark-${spark_version}/${spark_tarball}" -o "${spark_tarball}"
  fi
  printf '%s  %s\n' "${spark_sha256}" "${spark_tarball}" | sha256sum -c >/dev/null
  tar -xf "${spark_tarball}"
fi

printf "Downloading JDBC JARs and dependencies via Maven...\n"

for artifact in \
  com.microsoft.azure:adal4j:1.6.4:jar \
  com.nimbusds:oauth2-oidc-sdk:6.5:jar \
  com.google.code.gson:gson:2.8.0:jar \
  net.minidev:json-smart:1.3.1:jar \
  com.nimbusds:nimbus-jose-jwt:8.2.1:jar \
  org.slf4j:slf4j-api:1.7.21:jar \
  com.microsoft.sqlserver:mssql-jdbc:6.4.0.jre8:jar \
  com.oracle.database.jdbc:ojdbc8:${ORACLE_JDBC_VERSION}:jar \
  net.snowflake:snowflake-jdbc:${SNOWFLAKE_JDBC_VERSION}:jar \
  net.snowflake:spark-snowflake_2.12:${SNOWFLAKE_SPARK_VERSION}:jar
do
  mvn dependency:copy -q --strict-checksums -DoutputDirectory="$JARS_DIR" -Dartifact="${artifact}" &
done
wait

# The sql-spark-connector is not on Maven Central; download from GitHub.
curl -fsSL -o "$JARS_DIR/${mssql_jdbc}.jar" \
  "https://github.com/microsoft/sql-spark-connector/releases/download/v${mssql_jdbc_version}/${mssql_jdbc}.jar"
printf '%s  %s\n' "${mssql_jdbc_sha256}" "$JARS_DIR/${mssql_jdbc}.jar" | sha256sum -c >/dev/null

# --- Start Spark Connect server ---

rm -rf "${HOME}"/spark/"${spark}"/spark-warehouse
printf "Cleared old spark warehouse default directory\n"

cd "${spark}" || exit 1
result=$(${SERVER_SCRIPT} --packages "org.apache.spark:${spark_connect}:${spark_version}" > "$HOME"/spark/log.out; echo $?)

if [ "$result" -ne 0 ]; then
    count=$(tail "${HOME}"/spark/log.out | grep -c "SparkConnectServer running as process")
    if [ "${count}" == "0" ]; then
            printf "Failed to start the server\n"
        exit 1
    fi
    # Wait for the server to start by pinging localhost:4040
    printf "Waiting for the server to start...\n"
    for i in {1..30}; do
        if nc -z localhost 4040; then
            printf "Server is up and running\n"
            break
        fi
        printf "Server not yet available, retrying in 5 seconds...\n"
        sleep 5
    done

    if ! nc -z localhost 4040; then
        printf "Failed to start the server within the expected time\n"
        exit 1
    fi
fi
printf "Started the Server\n"
