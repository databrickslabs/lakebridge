from unittest.mock import create_autospec

import pytest
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import (
    CatalogInfo,
    EffectivePermissionsList,
    Privilege,
    SchemaInfo,
    VolumeInfo,
    SecurableType,
)
from databricks.sdk.errors import NotFound
from databricks.labs.lakebridge.helpers.metastore import CatalogOperations


@pytest.fixture
def ws():
    return create_autospec(WorkspaceClient)


def test_get_existing_catalog(ws):
    ws.catalogs.get.return_value = CatalogInfo(name="test")
    catalog_operations = CatalogOperations(ws)
    assert isinstance(catalog_operations.get_catalog("test"), CatalogInfo)


def test_get_non_existing_catalog(ws):
    ws.catalogs.get.side_effect = NotFound()
    catalog_operations = CatalogOperations(ws)
    assert catalog_operations.get_catalog("test") is None


def test_get_existing_schema(ws):
    ws.schemas.get.return_value = SchemaInfo(catalog_name="test_catalog", name="test_schema")
    catalog_operations = CatalogOperations(ws)
    assert isinstance(catalog_operations.get_schema("test_catalog", "test_schema"), SchemaInfo)


def test_get_non_existing_schema(ws):
    ws.schemas.get.side_effect = NotFound()
    catalog_operations = CatalogOperations(ws)
    assert catalog_operations.get_schema("test_catalog", "test_schema") is None


def test_get_existing_volume(ws):
    ws.volumes.read.return_value = VolumeInfo(
        catalog_name="test_catalog", schema_name="test_schema", name="test_volume"
    )
    catalog_operations = CatalogOperations(ws)
    assert isinstance(
        catalog_operations.get_volume("test_catalog", "test_schema", "test_volume"),
        VolumeInfo,
    )


def test_get_non_existing_volume(ws):
    ws.volumes.read.side_effect = NotFound()
    catalog_operations = CatalogOperations(ws)
    assert catalog_operations.get_volume("test_catalog", "test_schema", "test_volume") is None


def test_create_catalog(ws):
    catalog = CatalogInfo(name="test")
    ws.catalogs.create.return_value = catalog
    catalog_operations = CatalogOperations(ws)
    assert catalog == catalog_operations.create_catalog("test")


def test_create_schema(ws):
    schema = SchemaInfo(catalog_name="test_catalog", name="test_schema")
    ws.schemas.create.return_value = schema
    catalog_operations = CatalogOperations(ws)
    assert schema == catalog_operations.create_schema("test_schema", "test_catalog")


def test_create_volume(ws):
    volume = VolumeInfo(catalog_name="test_catalog", schema_name="test_schema", name="test_volume")
    ws.volumes.create.return_value = volume
    catalog_operations = CatalogOperations(ws)
    assert volume == catalog_operations.create_volume("test_catalog", "test_schema", "test_volume")


def test_has_all_privileges(ws):
    ws.grants.get_effective.return_value = EffectivePermissionsList.from_dict(
        {
            "privilege_assignments": [
                {
                    "privileges": [
                        {"privilege": "USE_CATALOG"},
                        {"privilege": "CREATE_SCHEMA"},
                    ],
                }
            ]
        }
    )

    catalog_ops = CatalogOperations(ws)
    assert catalog_ops.has_privileges(
        user='test_user',
        securable_type=SecurableType.CATALOG,
        full_name='test_catalog',
        privileges={Privilege.USE_CATALOG, Privilege.CREATE_SCHEMA},
    )


def test_has_no_privileges(ws):
    ws.grants.get_effective.return_value = EffectivePermissionsList.from_dict(
        {
            "privilege_assignments": [
                {
                    "privileges": [],
                }
            ]
        }
    )

    catalog_ops = CatalogOperations(ws)
    assert not catalog_ops.has_privileges(
        user='test_user',
        securable_type=SecurableType.CATALOG,
        full_name='test_catalog',
        privileges={Privilege.USE_CATALOG, Privilege.CREATE_SCHEMA},
    )


def test_has_none_permission_list(ws):
    ws.grants.get_effective.return_value = None
    catalog_ops = CatalogOperations(ws)
    assert not catalog_ops.has_privileges(
        user='test_user',
        securable_type=SecurableType.CATALOG,
        full_name='test_catalog',
        privileges={Privilege.USE_CATALOG, Privilege.CREATE_SCHEMA},
    )


def test_has_none_privilege_assignments(ws):
    ws.grants.get_effective.return_value = EffectivePermissionsList.from_dict({"privilege_assignments": None})
    catalog_ops = CatalogOperations(ws)
    assert not catalog_ops.has_privileges(
        user='test_user',
        securable_type=SecurableType.CATALOG,
        full_name='test_catalog',
        privileges={Privilege.USE_CATALOG, Privilege.CREATE_SCHEMA},
    )


def test_has_some_privileges(ws):
    ws.grants.get_effective.return_value = EffectivePermissionsList.from_dict(
        {
            "privilege_assignments": [
                {
                    "privileges": [{"privilege": "USE_CATALOG"}],
                }
            ]
        }
    )

    catalog_ops = CatalogOperations(ws)
    assert not catalog_ops.has_privileges(
        user='test_user',
        securable_type=SecurableType.CATALOG,
        full_name='test_catalog',
        privileges={Privilege.USE_CATALOG, Privilege.CREATE_SCHEMA},
    )


def test_has_catalog_access_owner(ws):
    catalog = CatalogInfo(name="test_catalog", owner="test_user@me.com")
    catalog_ops = CatalogOperations(ws)
    assert catalog_ops.has_catalog_access(catalog, "test_user@me.com", ({Privilege.ALL_PRIVILEGES},))


def test_has_catalog_access_has_all_privileges(ws):
    catalog = CatalogInfo(name="test_catalog")
    ws.grants.get_effective.return_value = EffectivePermissionsList.from_dict(
        {
            "privilege_assignments": [
                {
                    "privileges": [
                        {"privilege": "USE_CATALOG"},
                        {"privilege": "CREATE_SCHEMA"},
                    ],
                }
            ]
        }
    )
    catalog_ops = CatalogOperations(ws)
    assert catalog_ops.has_catalog_access(
        catalog, "test_user@me.com", ({Privilege.USE_CATALOG, Privilege.CREATE_SCHEMA},)
    )


def test_has_catalog_access_has_no_privileges(ws):
    catalog = CatalogInfo(name="test_catalog")
    ws.grants.get_effective.return_value = EffectivePermissionsList.from_dict(
        {
            "privilege_assignments": [
                {
                    "privileges": [],
                }
            ]
        }
    )
    catalog_ops = CatalogOperations(ws)
    assert not catalog_ops.has_catalog_access(
        catalog, "test_user@me.com", ({Privilege.USE_CATALOG, Privilege.CREATE_SCHEMA},)
    )


def test_has_schema_access_owner(ws):
    schema = SchemaInfo(catalog_name="test_catalog", name="test_schema", owner="test_user@me.com")
    catalog_ops = CatalogOperations(ws)
    assert catalog_ops.has_schema_access(
        schema,
        "test_user@me.com",
        ({Privilege.ALL_PRIVILEGES},),
    )


def test_has_schema_access_has_all_privileges(ws):
    schema = SchemaInfo(catalog_name="test_catalog", name="test_schema", full_name="test_catalog.test_schema")
    ws.grants.get_effective.return_value = EffectivePermissionsList.from_dict(
        {
            "privilege_assignments": [
                {
                    "privileges": [
                        {"privilege": "USE_SCHEMA"},
                        {"privilege": "CREATE_TABLE"},
                    ],
                }
            ]
        }
    )
    catalog_ops = CatalogOperations(ws)
    assert catalog_ops.has_schema_access(
        schema,
        "test_user@me.com",
        ({Privilege.USE_SCHEMA, Privilege.CREATE_TABLE},),
    )


def test_schema_access_has_no_privileges(ws):
    schema = SchemaInfo(catalog_name="test_catalog", name="test_schema", full_name="test_catalog.test_schema")
    ws.grants.get_effective.return_value = EffectivePermissionsList.from_dict(
        {
            "privilege_assignments": [
                {
                    "privileges": [],
                }
            ]
        }
    )
    catalog_ops = CatalogOperations(ws)
    assert not catalog_ops.has_schema_access(
        schema,
        "test_user@me.com",
        ({Privilege.USE_SCHEMA, Privilege.CREATE_TABLE},),
    )


def test_has_volume_access_owner(ws):
    volume = VolumeInfo(
        catalog_name="test_catalog", schema_name="test_schema", name="test_volume", owner="test_user@me.com"
    )
    catalog_ops = CatalogOperations(ws)
    assert catalog_ops.has_volume_access(
        volume,
        "test_user@me.com",
        ({Privilege.ALL_PRIVILEGES},),
    )


def test_has_volume_access_has_all_privileges(ws):
    volume = VolumeInfo(
        catalog_name="test_catalog",
        schema_name="test_schema",
        name="test_volume",
        full_name="test_catalog.test_schema.test_volume",
    )
    ws.grants.get_effective.return_value = EffectivePermissionsList.from_dict(
        {
            "privilege_assignments": [
                {
                    "privileges": [
                        {"privilege": "READ_VOLUME"},
                        {"privilege": "WRITE_VOLUME"},
                    ],
                }
            ]
        }
    )
    catalog_ops = CatalogOperations(ws)
    assert catalog_ops.has_volume_access(
        volume,
        "test_user@me.com",
        ({Privilege.READ_VOLUME, Privilege.WRITE_VOLUME},),
    )


def test_volume_access_has_no_privileges(ws):
    volume = VolumeInfo(
        catalog_name="test_catalog",
        schema_name="test_schema",
        name="test_volume",
        full_name="test_catalog.test_schema.test_volume",
    )
    ws.grants.get_effective.return_value = EffectivePermissionsList.from_dict(
        {
            "privilege_assignments": [
                {
                    "privileges": [],
                }
            ]
        }
    )
    catalog_ops = CatalogOperations(ws)
    assert not catalog_ops.has_volume_access(
        volume,
        "test_user@me.com",
        ({Privilege.READ_VOLUME, Privilege.WRITE_VOLUME},),
    )
