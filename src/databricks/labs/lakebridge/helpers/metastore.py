import functools
import logging
from itertools import chain

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service.catalog import (
    CatalogInfo,
    Privilege,
    SchemaInfo,
    SecurableType,
    VolumeInfo,
    VolumeType,
)

logger = logging.getLogger(__name__)


class CatalogOperations:
    def __init__(self, ws: WorkspaceClient):
        self._ws = ws

    def get_catalog(self, name: str) -> CatalogInfo | None:
        try:
            return self._ws.catalogs.get(name)
        except NotFound:
            return None

    def get_schema(self, catalog_name: str, schema_name: str) -> SchemaInfo | None:
        try:
            return self._ws.schemas.get(f"{catalog_name}.{schema_name}")
        except NotFound:
            return None

    def get_volume(self, catalog: str, schema: str, name: str) -> VolumeInfo | None:
        try:
            return self._ws.volumes.read(f"{catalog}.{schema}.{name}")
        except NotFound:
            return None

    def create_catalog(self, name: str) -> CatalogInfo:
        logger.debug(f"Creating catalog `{name}`.")
        catalog_info = self._ws.catalogs.create(name)
        logger.info(f"Created catalog `{name}`.")
        return catalog_info

    def create_schema(self, schema_name: str, catalog_name: str) -> SchemaInfo:
        logger.debug(f"Creating schema `{schema_name}` in catalog `{catalog_name}`.")
        schema_info = self._ws.schemas.create(schema_name, catalog_name)
        logger.info(f"Created schema `{schema_name}` in catalog `{catalog_name}`.")
        return schema_info

    def create_volume(
        self,
        catalog: str,
        schema: str,
        name: str,
        volume_type: VolumeType = VolumeType.MANAGED,
    ) -> VolumeInfo:
        logger.debug(f"Creating volume `{name}` in catalog `{catalog}` and schema `{schema}`")
        volume_info = self._ws.volumes.create(catalog, schema, name, volume_type)
        logger.info(f"Created volume `{name}` in catalog `{catalog}` and schema `{schema}`")
        return volume_info

    def has_catalog_access(
        self,
        catalog: CatalogInfo,
        user_name: str,
        privilege_sets: tuple[set[Privilege], ...],
    ) -> bool:
        """
        Check if a user has access to a catalog based on ownership or a set of privileges.
        :param catalog: A catalog to check access for.
        :param user_name: Username to check.
        :param privilege_sets: A tuple of sets, where each set contains Privilege objects.
                               The function checks if the user has any of these sets of privileges. For example:
                               ({Privilege.ALL_PRIVILEGES}, {Privilege.USE_CATALOG, Privilege.APPLY_TAG})
                               In this case, the user would need either ALL_PRIVILEGES,
                               or both USE_CATALOG and APPLY_TAG.
        """
        if user_name == catalog.owner:
            return True

        return any(
            self.has_privileges(user_name, SecurableType.CATALOG, catalog.name, privilege_set)
            for privilege_set in privilege_sets
        )

    def has_schema_access(
        self,
        schema: SchemaInfo,
        user_name: str,
        privilege_sets: tuple[set[Privilege], ...],
    ) -> bool:
        """
        Check if a user has access to a schema based on ownership or a set of privileges.
        :param schema: A schema to check access for.
        :param user_name: Username to check.
        :param privilege_sets: The function checks if the user has any of these sets of privileges. For example:
                               ({Privilege.ALL_PRIVILEGES}, {Privilege.USE_SCHEMA, Privilege.CREATE_TABLE})
                               In this case, the user would need either ALL_PRIVILEGES,
                               or both USE_SCHEMA and CREATE_TABLE.
        """
        if user_name == schema.owner:
            return True

        return any(
            self.has_privileges(user_name, SecurableType.SCHEMA, schema.full_name, privilege_set)
            for privilege_set in privilege_sets
        )

    def has_volume_access(
        self,
        volume: VolumeInfo,
        user_name: str,
        privilege_sets: tuple[set[Privilege], ...],
    ) -> bool:
        """
        Check if a user has access to a volume based on ownership or a set of privileges.
        :param volume: A volume to check access for.
        :param user_name: Username to check.
        :param privilege_sets: The function checks if the user has any of these sets of privileges. For example:
                               ({Privilege.ALL_PRIVILEGES}, {Privilege.READ_VOLUME, Privilege.WRITE_VOLUME})
                               In this case, the user would need either ALL_PRIVILEGES,
                               or both READ_VOLUME and WRITE_VOLUME.
        """
        if user_name == volume.owner:
            return True

        return any(
            self.has_privileges(user_name, SecurableType.VOLUME, volume.full_name, privilege_set)
            for privilege_set in privilege_sets
        )

    def has_privileges(
        self,
        user: str | None,
        securable_type: SecurableType,
        full_name: str | None,
        privileges: set[Privilege],
    ) -> bool:
        """
        Check if a user has a set of privileges for a securable object.
        """
        assert user, "User must be provided"
        assert full_name, "Full name must be provided"
        user_privileges = self._get_user_privileges(user, securable_type, full_name)
        result = privileges.issubset(user_privileges)
        if not result:
            logger.debug(f"User {user} doesn't have privilege set {privileges} for {securable_type} {full_name}")
        return result

    @functools.lru_cache(maxsize=1024)
    def _get_user_privileges(self, user: str, securable_type: SecurableType, full_name: str) -> set[Privilege]:
        permissions = self._ws.grants.get_effective(securable_type, full_name, principal=user)
        if not permissions or not permissions.privilege_assignments:
            return set()
        return {
            p.privilege
            for p in chain.from_iterable(
                privilege.privileges for privilege in permissions.privilege_assignments if privilege.privileges
            )
            if p.privilege
        }
