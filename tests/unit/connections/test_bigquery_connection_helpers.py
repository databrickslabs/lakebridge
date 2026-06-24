from unittest.mock import patch

import pytest
from google.api_core.exceptions import GoogleAPICallError
from databricks.labs.lakebridge.connections.bigquery_connection_helpers import validate_bigquery_pairs

_CLIENT = "databricks.labs.lakebridge.connections.bigquery_connection_helpers.bigquery.Client"


def test_validate_bigquery_pairs_probes_each_pair():
    with patch(_CLIENT) as client:
        client.return_value.__enter__.return_value = client.return_value
        validate_bigquery_pairs(
            {"pairs": [{"project": "proj-a", "region": "us"}, {"project": "proj-b", "region": "eu"}]}
        )
    assert client.call_count == 2
    client.assert_any_call(project="proj-a", location="us")
    client.assert_any_call(project="proj-b", location="eu")
    client.return_value.query.assert_called_with("SELECT 1")


def test_validate_bigquery_pairs_aggregates_failures():
    with patch(_CLIENT) as client:
        client.return_value.__enter__.return_value = client.return_value
        client.return_value.query.return_value.result.side_effect = GoogleAPICallError("boom")
        with pytest.raises(ConnectionError, match="Connection failed for BigQuery pairs"):
            validate_bigquery_pairs({"pairs": [{"project": "proj-a", "region": "us"}]})


def test_validate_bigquery_pairs_requires_pairs():
    with pytest.raises(ValueError, match="No BigQuery project/region pairs"):
        validate_bigquery_pairs({"pairs": []})
