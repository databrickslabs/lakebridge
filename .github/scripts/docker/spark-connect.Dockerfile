# 3.5.5
FROM apache/spark@sha256:39321d67b23e2e0953f81b60778f74bf40c40a18dfb0e881e6a38593af60afa1

USER root

# JDBC and connector JARs
ARG ORACLE_JDBC_VERSION=19.28.0.0
ARG MSSQL_JDBC_VERSION=1.4.0
ARG SNOWFLAKE_JDBC_VERSION=3.26.1
ARG SNOWFLAKE_SPARK_VERSION=2.11.2-spark_3.3

ARG SPARK_CONNECT_VERSION=3.5.5

COPY spark-jars.sha256sum /tmp/spark-jars.sha256sum

RUN mkdir -p /opt/spark/jars && \
    # Spark Connect
    wget -q "https://repo1.maven.org/maven2/org/apache/spark/spark-connect_2.12/${SPARK_CONNECT_VERSION}/spark-connect_2.12-${SPARK_CONNECT_VERSION}.jar" \
      -O /opt/spark/jars/spark-connect.jar && \
    # Oracle JDBC
    wget -q "https://repo1.maven.org/maven2/com/oracle/database/jdbc/ojdbc8/${ORACLE_JDBC_VERSION}/ojdbc8-${ORACLE_JDBC_VERSION}.jar" \
      -O /opt/spark/jars/ojdbc8.jar && \
    # MSSQL Spark connector + dependencies
    wget -q "https://github.com/microsoft/sql-spark-connector/releases/download/v${MSSQL_JDBC_VERSION}/spark-mssql-connector_2.12-${MSSQL_JDBC_VERSION}-BETA.jar" \
      -O /opt/spark/jars/spark-mssql-connector.jar && \
    wget -q "https://repo1.maven.org/maven2/com/microsoft/sqlserver/mssql-jdbc/6.4.0.jre8/mssql-jdbc-6.4.0.jre8.jar" \
      -O /opt/spark/jars/mssql-jdbc.jar && \
    wget -q "https://repo1.maven.org/maven2/com/microsoft/azure/adal4j/1.6.4/adal4j-1.6.4.jar" \
      -O /opt/spark/jars/adal4j.jar && \
    wget -q "https://repo1.maven.org/maven2/com/nimbusds/oauth2-oidc-sdk/6.5/oauth2-oidc-sdk-6.5.jar" \
      -O /opt/spark/jars/oauth2-oidc-sdk.jar && \
    wget -q "https://repo1.maven.org/maven2/com/google/code/gson/gson/2.8.0/gson-2.8.0.jar" \
      -O /opt/spark/jars/gson.jar && \
    wget -q "https://repo1.maven.org/maven2/net/minidev/json-smart/1.3.1/json-smart-1.3.1.jar" \
      -O /opt/spark/jars/json-smart.jar && \
    wget -q "https://repo1.maven.org/maven2/com/nimbusds/nimbus-jose-jwt/8.2.1/nimbus-jose-jwt-8.2.1.jar" \
      -O /opt/spark/jars/nimbus-jose-jwt.jar && \
    wget -q "https://repo1.maven.org/maven2/org/slf4j/slf4j-api/1.7.21/slf4j-api-1.7.21.jar" \
      -O /opt/spark/jars/slf4j-api.jar && \
    # Snowflake JDBC + Spark connector
    wget -q "https://repo1.maven.org/maven2/net/snowflake/snowflake-jdbc/${SNOWFLAKE_JDBC_VERSION}/snowflake-jdbc-${SNOWFLAKE_JDBC_VERSION}.jar" \
      -O /opt/spark/jars/snowflake-jdbc.jar && \
    wget -q "https://repo1.maven.org/maven2/net/snowflake/spark-snowflake_2.12/${SNOWFLAKE_SPARK_VERSION}/spark-snowflake_2.12-${SNOWFLAKE_SPARK_VERSION}.jar" \
      -O /opt/spark/jars/spark-snowflake.jar && \
    cd /opt/spark/jars && sha256sum -c /tmp/spark-jars.sha256sum

USER spark

EXPOSE 15002 4040

# start-connect-server.sh daemonizes; tail the log to keep the container alive
ENTRYPOINT ["/bin/bash", "-c", \
  "/opt/spark/sbin/start-connect-server.sh --conf spark.connect.grpc.binding.port=15002 && tail -f /opt/spark/logs/*.out"]
