#!/usr/bin/env bash

set -xve

mkdir -p "$HOME"/spark
cd "$HOME"/spark || exit 1

version=$(wget -O - https://downloads.apache.org/spark/ | grep 'href="spark-3\.[0-9.]*/"' | sed 's:</a>:\n:g' | sed -n 's/.*>//p' | tr -d spark/- | sort -r --version-sort | head -1)
if [ -z "$version" ]; then
  echo "Failed to extract Spark version"
   exit 1
fi

spark=spark-${version}-bin-hadoop3
spark_connect="spark-connect_2.12"
mssql_jdbc_version="1.4.0"
mssql_jdbc="spark-mssql-connector_2.12-${mssql_jdbc_version}-BETA"
mkdir -p "${spark}"


SERVER_SCRIPT=$HOME/spark/${spark}/sbin/start-connect-server.sh

## check the spark version already exist ,if not download the respective version
if [ -f "${SERVER_SCRIPT}" ];then
  echo "Spark Version already exists"
else
  if [ -f "${spark}.tgz" ];then
    echo "${spark}.tgz already exists"
  else
    wget "https://downloads.apache.org/spark/spark-${version}/${spark}.tgz"
  fi
  tar -xvf "${spark}.tgz"
fi

JARS_DIR=$HOME/spark/${spark}/jars
MSSQL_JDBC_JAR=$HOME/spark/${spark}/jars/${mssql_jdbc}.jar
if [ -f "${MSSQL_JDBC_JAR}" ];then
  echo "MSSQL JAR already exists"
else
  echo "Downloading MSSQL JAR and dependencies"
  ## fixes ClassNotFoundException: com.microsoft.sqlserver.jdbc.SQLServerDriver
  ## per https://github.com/microsoft/sql-spark-connector/issues/26#issuecomment-686155736
  wget https://repo1.maven.org/maven2/com/microsoft/azure/adal4j/1.6.4/adal4j-1.6.4.jar -O "$JARS_DIR"/adal4j-1.6.4.jar
  wget https://repo1.maven.org/maven2/com/nimbusds/oauth2-oidc-sdk/6.5/oauth2-oidc-sdk-6.5.jar -O "$JARS_DIR"/oauth2-oidc-sdk-6.5.jar
  wget https://repo1.maven.org/maven2/com/google/code/gson/gson/2.8.0/gson-2.8.0.jar -O "$JARS_DIR"/gson-2.8.0.jar
  wget https://repo1.maven.org/maven2/net/minidev/json-smart/1.3.1/json-smart-1.3.1.jar -O "$JARS_DIR"/json-smart-1.3.1.jar
  wget https://repo1.maven.org/maven2/com/nimbusds/nimbus-jose-jwt/8.2.1/nimbus-jose-jwt-8.2.1.jar -O "$JARS_DIR"/nimbus-jose-jwt-8.2.1.jar
  wget https://repo1.maven.org/maven2/org/slf4j/slf4j-api/1.7.21/slf4j-api-1.7.21.jar -O "$JARS_DIR"/slf4j-api-1.7.21.jar
  wget https://repo1.maven.org/maven2/com/microsoft/sqlserver/mssql-jdbc/6.4.0.jre8/mssql-jdbc-6.4.0.jre8.jar -O "$JARS_DIR"/mssql-jdbc-6.4.0.jre8.jar
  wget "https://github.com/microsoft/sql-spark-connector/releases/download/v${mssql_jdbc_version}/${mssql_jdbc}.jar" -O "$JARS_DIR"/${mssql_jdbc}.jar
fi

cd "${spark}" || exit 1
## check spark remote is running,if not start the spark remote
## Temporary workaround for Spark Connect server still points to 3.5.5
result=$(${SERVER_SCRIPT} --packages org.apache.spark:${spark_connect}:"3.5.5" > "$HOME"/spark/log.out; echo $?)

if [ "$result" -ne 0 ]; then
    count=$(tail "${HOME}"/spark/log.out | grep -c "SparkConnectServer running as process")
    if [ "${count}" == "0" ]; then
            echo "Failed to start the server"
        exit 1
    fi
    # Wait for the server to start by pinging localhost:4040
    echo "Waiting for the server to start..."
    for i in {1..30}; do
        if nc -z localhost 4040; then
            echo "Server is up and running"
            break
        fi
        echo "Server not yet available, retrying in 5 seconds..."
        sleep 5
    done

    if ! nc -z localhost 4040; then
        echo "Failed to start the server within the expected time"
        exit 1
    fi
fi
echo "Started the Server"
