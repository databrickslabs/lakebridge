from unittest.mock import create_autospec

import pytest
from databricks.labs.blueprint.tui import MockPrompts
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import iam
from databricks.sdk.service.catalog import (
    CatalogInfo,
    SchemaInfo,
    VolumeInfo,
)
from databricks.sdk.service.sql import EndpointInfo, EndpointInfoWarehouseType, GetWarehouseResponse, State

from databricks.labs.lakebridge.deployment.configurator import ResourceConfigurator
from databricks.labs.lakebridge.helpers.metastore import CatalogOperations


@pytest.fixture
def ws():
    w = create_autospec(WorkspaceClient)
    w.current_user.me.side_effect = lambda: iam.User(
        user_name="me@example.com", groups=[iam.ComplexValue(display="admins")]
    )
    return w


def test_prompt_for_catalog_setup_existing_catalog(ws):
    prompts = MockPrompts(
        {
            r"Enter catalog name": "remorph",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_catalog.return_value = CatalogInfo(name="remorph")
    catalog_operations.has_catalog_access.return_value = True
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    assert configurator.prompt_for_catalog_setup() == "remorph"


def test_prompt_for_catalog_setup_existing_catalog_no_access_abort(ws):
    prompts = MockPrompts(
        {
            r"Enter catalog name": "remorph",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_catalog.return_value = CatalogInfo(name="remorph")
    catalog_operations.get_schema.return_value = SchemaInfo(catalog_name="remorph", name="reconcile")
    catalog_operations.has_catalog_access.return_value = False
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    with pytest.raises(SystemExit):
        configurator.prompt_for_catalog_setup()
        configurator.has_necessary_access("remorph", "reconcile", None)


def test_prompt_for_catalog_setup_existing_catalog_no_access_retry_exhaust_attempts(ws):
    prompts = MockPrompts(
        {
            r"Enter catalog name": "remorph",
            r"Catalog .* doesn't exist. Create it?": "no",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_catalog.return_value = None
    catalog_operations.has_catalog_access.return_value = False
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    with pytest.raises(SystemExit):
        configurator.prompt_for_catalog_setup()


def test_prompt_for_catalog_setup_new_catalog(ws):
    prompts = MockPrompts(
        {
            r"Enter catalog name": "remorph",
            r"Catalog .* doesn't exist. Create it?": "yes",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_catalog.return_value = None
    catalog_operations.create_catalog.return_value = CatalogInfo(name="remorph")
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    assert configurator.prompt_for_catalog_setup() == "remorph"


def test_prompt_for_catalog_setup_new_catalog_abort(ws):
    prompts = MockPrompts(
        {
            r"Enter catalog name": "remorph",
            r"Catalog .* doesn't exist. Create it?": "no",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_catalog.return_value = None
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    with pytest.raises(SystemExit):
        configurator.prompt_for_catalog_setup()


def test_prompt_for_schema_setup_existing_schema(ws):
    prompts = MockPrompts(
        {
            r"Enter schema name": "reconcile",
        }
    )
    catalog_ops = create_autospec(CatalogOperations)
    catalog_ops.get_schema.return_value = SchemaInfo(
        catalog_name="remorph",
        name="reconcile",
        full_name="remorph.reconcile",
    )
    catalog_ops.has_schema_access.return_value = True
    configurator = ResourceConfigurator(ws, prompts, catalog_ops)
    assert configurator.prompt_for_schema_setup("remorph", "reconcile") == "reconcile"


def test_prompt_for_schema_setup_existing_schema_no_access_abort(ws):
    prompts = MockPrompts(
        {
            r"Enter schema name": "remorph",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_schema.return_value = SchemaInfo(
        catalog_name="remorph", name="reconcile", full_name="remorph.reconcile"
    )
    catalog_operations.has_catalog_access.return_value = True
    catalog_operations.has_schema_access.return_value = False
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    with pytest.raises(SystemExit):
        configurator.prompt_for_schema_setup("remorph", "reconcile")
        configurator.has_necessary_access("remorph", "reconcile", None)


def test_prompt_for_schema_setup_existing_schema_no_access_retry_exhaust_attempts(ws):
    prompts = MockPrompts(
        {
            r"Enter schema name": "remorph",
            r"Schema .* doesn't exist .* Create it?": "no",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_schema.return_value = None
    catalog_operations.has_schema_access.return_value = False
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    with pytest.raises(SystemExit):
        configurator.prompt_for_schema_setup("remorph", "reconcile")


def test_prompt_for_schema_setup_new_schema(ws):
    prompts = MockPrompts(
        {
            r"Enter schema name": "remorph",
            r"Schema .* doesn't exist .* Create it?": "yes",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_schema.return_value = None
    catalog_operations.create_schema.return_value = SchemaInfo(catalog_name="remorph", name="reconcile")
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    assert configurator.prompt_for_schema_setup("remorph", "reconcile") == "reconcile"


def test_prompt_for_schema_setup_new_schema_abort(ws):
    prompts = MockPrompts(
        {
            r"Enter schema name": "remorph",
            r"Schema .* doesn't exist .* Create it?": "no",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_schema.return_value = None
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    with pytest.raises(SystemExit):
        configurator.prompt_for_schema_setup("remorph", "reconcile")


def test_prompt_for_volume_setup_existing_volume(ws):
    prompts = MockPrompts(
        {
            r"Enter volume name": "recon_volume",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_volume.return_value = VolumeInfo(
        catalog_name="remorph",
        schema_name="reconcile",
        name="recon_volume",
    )
    catalog_operations.has_volume_access.return_value = True
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    assert (
        configurator.prompt_for_volume_setup(
            "remorph",
            "reconcile",
            "recon_volume",
        )
        == "recon_volume"
    )


def test_prompt_for_volume_setup_existing_volume_no_access_abort(ws):
    prompts = MockPrompts(
        {
            r"Enter volume name": "recon_volume",
            r"Do you want to use another volume?": "no",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_volume.return_value = VolumeInfo(
        catalog_name="remorph",
        schema_name="reconcile",
        name="recon_volume",
        full_name="remorph.reconcile.recon_volume",
    )
    catalog_operations.has_volume_access.return_value = False
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    with pytest.raises(SystemExit):
        configurator.prompt_for_volume_setup(
            "remorph",
            "reconcile",
            "recon_volume",
        )
        configurator.has_necessary_access("remorph", "reconcile", "recon_volume")


def test_prompt_for_volume_setup_existing_volume_no_access_retry_exhaust_attempts(ws):
    prompts = MockPrompts(
        {
            r"Enter volume name": "recon_volume",
            r"Volume .* doesn't exist .* Create it?": "no",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_volume.return_value = None
    catalog_operations.has_volume_access.return_value = False
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    with pytest.raises(SystemExit):
        configurator.prompt_for_volume_setup(
            "remorph",
            "reconcile",
            "recon_volume",
        )


def test_prompt_for_volume_setup_new_volume(ws):
    prompts = MockPrompts(
        {
            r"Enter volume name": "recon_volume",
            r"Volume .* doesn't exist .* Create it?": "yes",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_volume.return_value = None
    catalog_operations.create_volume.return_value = VolumeInfo(
        catalog_name="remorph",
        schema_name="reconcile",
        name="recon_volume",
    )
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    assert (
        configurator.prompt_for_volume_setup(
            "remorph",
            "reconcile",
            "recon_volume",
        )
        == "recon_volume"
    )


def test_prompt_for_volume_setup_new_volume_abort(ws):
    prompts = MockPrompts(
        {
            r"Enter volume name": "recon_volume",
            r"Volume .* doesn't exist .* Create it?": "no",
        }
    )
    catalog_operations = create_autospec(CatalogOperations)
    catalog_operations.get_volume.return_value = None
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    with pytest.raises(SystemExit):
        configurator.prompt_for_volume_setup(
            "remorph",
            "reconcile",
            "recon_volume",
        )


def test_prompt_for_warehouse_setup_from_existing_warehouses(ws):
    ws.warehouses.list.return_value = [
        EndpointInfo(
            name="Test Warehouse",
            id="w_id",
            warehouse_type=EndpointInfoWarehouseType.PRO,
            state=State.RUNNING,
        )
    ]
    prompts = MockPrompts({r"Select PRO or SERVERLESS SQL warehouse": "1"})
    catalog_operations = create_autospec(CatalogOperations)
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    assert configurator.prompt_for_warehouse_setup("Test") == "w_id"


def test_prompt_for_warehouse_setup_new(ws):
    ws.warehouses.list.return_value = [
        EndpointInfo(
            name="Test Warehouse",
            id="w_id",
            warehouse_type=EndpointInfoWarehouseType.PRO,
            state=State.RUNNING,
        )
    ]
    ws.warehouses.create.return_value = GetWarehouseResponse(id="new_w_id")
    prompts = MockPrompts({r"Select PRO or SERVERLESS SQL warehouse": "0"})
    catalog_operations = create_autospec(CatalogOperations)
    configurator = ResourceConfigurator(ws, prompts, catalog_operations)
    assert configurator.prompt_for_warehouse_setup("Test") == "new_w_id"
