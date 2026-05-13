import json
from dataclasses import asdict
from unittest.mock import MagicMock, create_autospec, patch

from databricks.labs.blueprint.installation import Installation

from databricks.labs.lakebridge.config import TableRecon
from databricks.labs.lakebridge.reconcile.config_generator.configure import configure_tables
from databricks.labs.lakebridge.reconcile.recon_config import Table

from tests.conftest import schema_fixture_factory


def test_configure_tables_uploads_to_canonical_filename(make_data_source, reconcile_config):
    source_ds = make_data_source(
        tables={("src_cat", "src_schema"): ["employees"]},
        columns={("src_cat", "src_schema", "employees"): [schema_fixture_factory("emp_id", "int")]},
    )
    target_ds = make_data_source(
        tables={("tgt_cat", "tgt_schema"): ["employees"]},
        columns={("tgt_cat", "tgt_schema", "employees"): [schema_fixture_factory("emp_id", "int")]},
    )

    installation = create_autospec(Installation)
    config = reconcile_config()

    with patch(
        "databricks.labs.lakebridge.reconcile.config_generator.configure.create_adapter",
        side_effect=[source_ds, target_ds],
    ):
        result = configure_tables(
            installation=installation,
            reconcile_config=config,
            spark=MagicMock(),
        )

    assert result.tables == [Table(source_name="employees", target_name="employees")]

    installation.upload.assert_called_once()
    filename, payload = installation.upload.call_args.args
    assert filename == "recon_config_snowflake_my_conn_all.json"
    parsed = json.loads(payload.decode())
    assert parsed == asdict(TableRecon(tables=[Table(source_name="employees", target_name="employees")]))
