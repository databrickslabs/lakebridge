import argparse
import json
import sys
import logging
from azure.identity import DefaultAzureCredential
from azure.monitor.query import MetricsQueryClient
from azure.synapse.artifacts import ArtifactsClient


def set_logger(name: str = __name__) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    handler = logging.StreamHandler(sys.stderr)
    log.setLevel(logging.INFO)
    log.addHandler(handler)

    log.propagate = False
    return log


logger = set_logger(__name__)


def arguments_loader(desc: str):
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument('--db-path', type=str, required=True, help='Path to DuckDB database file')
    parser.add_argument(
        '--credential-config-path', type=str, required=True, help='Path string containing credential configuration'
    )
    args = parser.parse_args()
    credential_file = args.credential_config_path

    if not credential_file.endswith('credentials.yml'):
        msg = "Credential config file must have 'credentials.yml' extension"
        # This is the output format expected by the pipeline.py which orchestrates the execution of this script
        print(json.dumps({"status": "error", "message": msg}), file=sys.stderr)
        raise ValueError("Credential config file must have 'credentials.yml' extension")

    return args.db_path, credential_file


def create_synapse_artifacts_client(config: dict) -> ArtifactsClient:
    """
    :return:  an Azure SDK client handle for Synapse Artifacts
    """
    return ArtifactsClient(
        endpoint=config["azure_api_access"]["development_endpoint"], credential=DefaultAzureCredential()
    )


def create_azure_metrics_query_client():
    """
    :return: an Azure SDK Monitoring Metrics Client handle
    """
    return MetricsQueryClient(credential=DefaultAzureCredential())
