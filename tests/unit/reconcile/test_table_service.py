import pytest

from databricks.labs.lakebridge.reconcile.connectors.data_source import MockDataSource
from databricks.labs.lakebridge.reconcile.normalize_recon_config_service import NormalizeReconConfigService


@pytest.fixture
def datasource():
    return MockDataSource({}, {})


@pytest.fixture
def normalize_service(datasource):
    return NormalizeReconConfigService(datasource, datasource)


def test_normalize_recon_table_config_uses_data_source(normalize_service, table_conf_with_opts):
    result = normalize_service.normalize_recon_table_config(table_conf_with_opts)

    assert result == table_conf_with_opts
