import logging
import time

from databricks.labs.blueprint.tui import Prompts
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import Privilege, SecurableType
from databricks.sdk.service.sql import (
    CreateWarehouseRequestWarehouseType,
    EndpointInfoWarehouseType,
    SpotInstancePolicy,
)

from databricks.labs.lakebridge.helpers.metastore import CatalogOperations

logger = logging.getLogger(__name__)


class ResourceConfigurator:
    """
    Handles the setup of common Databricks resources like
    catalogs, schemas, volumes, and warehouses used across remorph modules.
    """

    def __init__(self, ws: WorkspaceClient, prompts: Prompts, catalog_ops: CatalogOperations):
        self._ws = ws
        self._user = ws.current_user.me()
        self._prompts = prompts
        self._catalog_ops = catalog_ops

    def prompt_for_catalog_setup(
        self,
    ) -> str:
        catalog_name = self._prompts.question("Enter catalog name", default="remorph")
        catalog = self._catalog_ops.get_catalog(catalog_name)
        if catalog:
            logger.info(f"Found existing catalog `{catalog_name}`")
            return catalog_name
        if self._prompts.confirm(f"Catalog `{catalog_name}` doesn't exist. Create it?"):
            result = self._catalog_ops.create_catalog(catalog_name)
            assert result.name is not None
            return result.name
        raise SystemExit("Cannot continue installation, without a valid catalog, Aborting the installation.")

    def prompt_for_schema_setup(
        self,
        catalog: str,
        default_schema_name: str,
    ) -> str:
        schema_name = self._prompts.question("Enter schema name", default=default_schema_name)
        schema = self._catalog_ops.get_schema(catalog, schema_name)
        if schema:
            logger.info(f"Found existing schema `{schema_name}` in catalog `{catalog}`")
            return schema_name
        if self._prompts.confirm(f"Schema `{schema_name}` doesn't exist in catalog `{catalog}`. Create it?"):
            result = self._catalog_ops.create_schema(schema_name, catalog)
            assert result.name is not None
            return result.name
        raise SystemExit("Cannot continue installation, without a valid schema. Aborting the installation.")

    def prompt_for_volume_setup(
        self,
        catalog: str,
        schema: str,
        default_volume_name: str,
    ) -> str:
        volume_name = self._prompts.question("Enter volume name", default=default_volume_name)
        volume = self._catalog_ops.get_volume(catalog, schema, volume_name)
        if volume:
            logger.info(f"Found existing volume `{volume_name}` in catalog `{catalog}` and schema `{schema}`")
            return volume_name
        if self._prompts.confirm(
            f"Volume `{volume_name}` doesn't exist in catalog `{catalog}` and schema `{schema}`. Create it?"
        ):
            result = self._catalog_ops.create_volume(catalog, schema, volume_name)
            assert result.name is not None
            return result.name
        raise SystemExit("Cannot continue installation, without a valid volume. Aborting the installation.")

    def prompt_for_warehouse_setup(self, warehouse_name_prefix: str) -> str:
        def warehouse_type(_):
            return _.warehouse_type.value if not _.enable_serverless_compute else "SERVERLESS"

        pro_warehouses = {"[Create new PRO SQL warehouse]": "create_new"} | {
            f"{_.name} ({_.id}, {warehouse_type(_)}, {_.state.value})": _.id
            for _ in self._ws.warehouses.list()
            if _.warehouse_type == EndpointInfoWarehouseType.PRO
        }
        warehouse_id = self._prompts.choice_from_dict(
            "Select PRO or SERVERLESS SQL warehouse",
            pro_warehouses,
        )
        if warehouse_id == "create_new":
            new_warehouse = self._ws.warehouses.create(
                name=f"{warehouse_name_prefix} {time.time_ns()}",
                spot_instance_policy=SpotInstancePolicy.COST_OPTIMIZED,
                warehouse_type=CreateWarehouseRequestWarehouseType.PRO,
                cluster_size="Small",
                max_num_clusters=1,
            )
            warehouse_id = new_warehouse.id
        return warehouse_id

    def has_necessary_catalog_access(
        self, catalog_name: str, user_name: str, privilege_sets: tuple[set[Privilege], ...]
    ):
        catalog = self._catalog_ops.get_catalog(catalog_name)
        assert catalog, f"Catalog not found {catalog_name}"
        if self._catalog_ops.has_catalog_access(catalog, user_name, privilege_sets):
            return True
        missing_permissions = self._get_missing_permissions(
            user_name, SecurableType.CATALOG, catalog.name, privilege_sets
        )
        logger.error(
            f"User `{user_name}` doesn't have required privileges :: \n`{missing_permissions}`\n to access catalog `{catalog_name}` "
        )
        return False

    def has_necessary_schema_access(
        self, catalog_name: str, schema_name: str, user_name: str, privilege_sets: tuple[set[Privilege], ...]
    ):
        schema = self._catalog_ops.get_schema(catalog_name, schema_name)
        assert schema, f"Schema not found {catalog_name}.{schema_name}"
        if self._catalog_ops.has_schema_access(schema, user_name, privilege_sets):
            return True
        missing_permissions = self._get_missing_permissions(
            user_name, SecurableType.SCHEMA, schema.full_name, privilege_sets
        )
        logger.error(
            f"User `{user_name}` doesn't have required privileges :: \n`{missing_permissions}`\n to access schema `{schema.full_name}` "
        )
        return False

    def has_necessary_volume_access(
        self,
        catalog_name: str,
        schema_name: str,
        volume_name: str,
        user_name: str,
        privilege_sets: tuple[set[Privilege], ...],
    ):
        volume = self._catalog_ops.get_volume(catalog_name, schema_name, volume_name)
        assert volume, f"Volume not found {catalog_name}.{schema_name}.{volume_name}"
        if self._catalog_ops.has_volume_access(volume, user_name, privilege_sets):
            return True
        missing_permissions = self._get_missing_permissions(
            user_name, SecurableType.VOLUME, volume.full_name, privilege_sets
        )
        logger.error(
            f"User `{user_name}` doesn't have required privileges :: \n`{missing_permissions}`\n to access volume `{volume.full_name}` "
        )
        return False

    def _get_missing_permissions(
        self,
        user_name: str,
        securable_type: SecurableType,
        resource_name: str | None,
        privilege_sets: tuple[set[Privilege], ...],
    ):
        assert resource_name, f"Catalog Resource name must be provided {resource_name}"
        missing_permissions_list = []
        for privilege_set in privilege_sets:
            permissions = self._catalog_ops.has_privileges(user_name, securable_type, resource_name, privilege_set)
            if not permissions:
                missing_privileges = ", ".join([privilege.name for privilege in privilege_set])
                missing_permissions_list.append(f" * '{missing_privileges}' ")

        return " OR \n".join(missing_permissions_list)

    def has_necessary_access(self, catalog_name: str, schema_name: str, volume_name: str | None):
        catalog_required_privileges: tuple[set[Privilege], ...] = (
            {Privilege.ALL_PRIVILEGES},
            {Privilege.USE_CATALOG},
        )
        schema_required_privileges: tuple[set[Privilege], ...] = (
            {Privilege.ALL_PRIVILEGES},
            {Privilege.USE_SCHEMA, Privilege.MODIFY, Privilege.SELECT, Privilege.CREATE_VOLUME},
            {Privilege.USE_SCHEMA, Privilege.MODIFY, Privilege.SELECT},
        )
        volume_required_privileges: tuple[set[Privilege], ...] = (
            {Privilege.ALL_PRIVILEGES},
            {Privilege.READ_VOLUME, Privilege.WRITE_VOLUME},
        )

        user_name = self._user.user_name
        assert user_name is not None

        catalog_access = self.has_necessary_catalog_access(catalog_name, user_name, catalog_required_privileges)
        schema_access = self.has_necessary_schema_access(
            catalog_name, schema_name, user_name, schema_required_privileges
        )
        required_access = catalog_access and schema_access
        if volume_name:
            volume_access = self.has_necessary_volume_access(
                catalog_name, schema_name, volume_name, user_name, volume_required_privileges
            )
            required_access = required_access and volume_access
        if not required_access:
            raise SystemExit("Cannot continue installation, without necessary access. Aborting the installation.")
