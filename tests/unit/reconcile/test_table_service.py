from unittest.mock import create_autospec

import pytest

from databricks.labs.lakebridge.reconcile.connectors.data_source import MockDataSource
from databricks.labs.lakebridge.reconcile.connectors.databricks import DatabricksDataSource
from databricks.labs.lakebridge.reconcile.connectors.models import NormalizedIdentifier
from databricks.labs.lakebridge.reconcile.normalize_recon_config_service import NormalizeReconConfigService
from databricks.labs.lakebridge.reconcile.recon_config import SamplingOptions


@pytest.fixture
def datasource():
    return MockDataSource({}, {})


@pytest.fixture
def normalize_service(datasource):
    return NormalizeReconConfigService(datasource, datasource)


@pytest.fixture
def databricks_source():
    src = create_autospec(DatabricksDataSource, instance=True)
    src.normalize_identifier.side_effect = lambda c: NormalizedIdentifier(
        ansi_normalized=c.lower(), source_normalized=f"`{c.lower()}`"
    )
    return src


@pytest.fixture
def databricks_normalize_service(databricks_source):
    return NormalizeReconConfigService(databricks_source, databricks_source)


def test_normalize_recon_table_config_uses_data_source(normalize_service, table_conf):
    raw = table_conf(join_columns=["id"], select_columns=["id", "name"], filter="id > 10")
    expected = table_conf(join_columns=["`id`"], select_columns=["`id`", "`name`"], filter="id > 10")

    result = normalize_service.normalize_recon_table_config(raw)

    assert result == expected


# --- Databricks source: clamps to [1, 50_000]; <=0 floors to 50 ---


@pytest.mark.parametrize(
    "requested,expected",
    [
        (1, 1),
        (49, 49),
        (50, 50),
        (200, 200),
        (50_000, 50_000),
        (50_001, 50_000),
        (1_000_000, 50_000),
    ],
)
def test_databricks_source_keeps_or_caps_max_sample_size(
    databricks_normalize_service, table_conf, requested, expected
):
    raw = table_conf(sampling_options=SamplingOptions(max_sample_size=requested))
    result = databricks_normalize_service.normalize_recon_table_config(raw)
    assert result.sampling_options.max_sample_size == expected
    assert result.sampling_options.specifications.value == expected


@pytest.mark.parametrize("requested", [0, -1, -100])
def test_databricks_source_floors_non_positive_to_default(
    databricks_normalize_service, table_conf, requested
):
    raw = table_conf(sampling_options=SamplingOptions(max_sample_size=requested))
    result = databricks_normalize_service.normalize_recon_table_config(raw)
    assert result.sampling_options.max_sample_size == 50
    assert result.sampling_options.specifications.value == 50


# --- Non-Databricks source: clamps to [1, 50]; <=0 floors to 50; >50 capped with warning ---


@pytest.mark.parametrize(
    "requested,expected",
    [
        (1, 1),
        (30, 30),
        (49, 49),
        (50, 50),
    ],
)
def test_non_databricks_source_keeps_within_50(normalize_service, table_conf, requested, expected):
    raw = table_conf(sampling_options=SamplingOptions(max_sample_size=requested))
    result = normalize_service.normalize_recon_table_config(raw)
    assert result.sampling_options.max_sample_size == expected
    assert result.sampling_options.specifications.value == expected


@pytest.mark.parametrize("requested", [0, -1, -100])
def test_non_databricks_source_floors_non_positive_to_default(normalize_service, table_conf, requested):
    raw = table_conf(sampling_options=SamplingOptions(max_sample_size=requested))
    result = normalize_service.normalize_recon_table_config(raw)
    assert result.sampling_options.max_sample_size == 50


@pytest.mark.parametrize("requested", [51, 200, 50_001])
def test_non_databricks_source_caps_above_50_with_warning(normalize_service, table_conf, requested, caplog):
    raw = table_conf(sampling_options=SamplingOptions(max_sample_size=requested))
    with caplog.at_level("WARNING"):
        result = normalize_service.normalize_recon_table_config(raw)
    assert result.sampling_options.max_sample_size == 50
    assert any("only honored for Databricks source" in rec.message for rec in caplog.records)


def test_no_sampling_options_is_passthrough(normalize_service, table_conf):
    raw = table_conf()
    result = normalize_service.normalize_recon_table_config(raw)
    assert result.sampling_options is None
