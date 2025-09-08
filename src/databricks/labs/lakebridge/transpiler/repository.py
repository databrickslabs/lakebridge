from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence, Set
from json import loads
import logging
import os
from typing import Any
from pathlib import Path

from databricks.labs.lakebridge.config import LSPConfigOptionV1

from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPConfig

logger = logging.getLogger(__name__)


class TranspilerRepository:
    """
    Repository for managing the installed transpilers in the user's home directory.

    The default repository for a user is always located under ~/.databricks/labs, and can be obtained
    via the `TranspilerRepository.user_home()` method.
    """

    #
    # Transpilers currently have different 'names', for historical reasons:
    #
    #  - product_name: the name of the product according to this project, assigned within the `installer` module and
    #       used as the name of the directory into which the transpiler is installed within a repository.
    #  - transpiler_name: the name of the product according to its own metadata, found in the configuration file
    #       bundled within each transpiler as distributed.
    #
    # Note: multiple installed transpilers might have the same transpiler name, but a product name is unique to a single
    # installed transpiler.
    #
    #  Known names at the moment:
    #
    #   - Morpheus:     product_name = databricks-morph-plugin,  transpiler_name = Morpheus
    #   - BladeBridge:  product_name = bladebridge,              transpiler_name = Bladebridge
    #

    @staticmethod
    def default_labs_path() -> Path:
        """Return the default path where labs applications are installed."""
        return Path.home() / ".databricks" / "labs"

    _default_repository: TranspilerRepository | None = None

    @classmethod
    def user_home(cls) -> TranspilerRepository:
        """The default repository for transpilers in the current user's home directory."""
        repository = cls._default_repository
        if repository is None:
            cls._default_repository = repository = cls(cls.default_labs_path())
        return repository

    def __init__(self, labs_path: Path) -> None:
        """Initialize the repository, based in the given location.

        This should only be used directly by tests; for the default repository, use `TranspilerRepository.user_home()`.

        Args:
            labs_path: The path where the labs applications are installed.
        """
        if self._default_repository == self and labs_path == self.default_labs_path():
            raise ValueError("Use TranspilerRepository.user_home() to get the default repository.")
        self._labs_path = labs_path

    def __repr__(self) -> str:
        return f"TranspilerRepository(labs_path={self._labs_path!r})"

    def transpilers_path(self) -> Path:
        return self._labs_path / "remorph-transpilers"

    def get_installed_version(self, product_name: str) -> str | None:
        """
        Obtain the version of an installed transpiler.

        Args:
          product_name: The product name of the transpiler whose version is sought.
        Returns:
          The version of the transpiler if it is installed, or None otherwise.
        """
        # Warning: product_name here (eg. 'morpheus') and transpiler_name elsewhere (eg. Morpheus) are not the same!
        product_path = self.transpilers_path() / product_name
        current_version_path = product_path / "state" / "version.json"
        try:
            text = current_version_path.read_text("utf-8")
        except FileNotFoundError:
            return None
        data: dict[str, Any] = loads(text)
        version: str | None = data.get("version", None)
        if not version or not version.startswith("v"):
            return None
        return version[1:]

    def all_transpiler_configs(self) -> Mapping[str, LSPConfig]:
        """Obtain all installed transpile configurations.

        Returns:
          A mapping of configurations, keyed by their transpiler names.
        """
        all_configs = self._all_transpiler_configs()
        return {config.name: config for config in all_configs}

    def all_transpiler_names(self) -> Set[str]:
        """Query the set of transpiler names for all installed transpilers."""
        all_configs = self.all_transpiler_configs()
        return frozenset(all_configs.keys())

    def all_dialects(self) -> Set[str]:
        """Query the set of dialects for all installed transpilers."""
        all_dialects: set[str] = set()
        for config in self._all_transpiler_configs():
            all_dialects = all_dialects.union(config.remorph.dialects)
        return all_dialects

    def transpilers_with_dialect(self, dialect: str) -> Set[str]:
        """
        Query the set of transpilers that can handle a given dialect.

        Args:
          dialect: The dialect to check for.
        Returns:
          The set of transpiler names for installed transpilers that can handle a given dialect.
        """
        configs = filter(lambda cfg: dialect in cfg.remorph.dialects, self.all_transpiler_configs().values())
        return frozenset(config.name for config in configs)

    def transpiler_config_path(self, transpiler_name: str) -> Path:
        """
        Obtain the path to a configuration file for an installed transpiler.

        Args:
          transpiler_name: The transpiler name of the transpiler whose path is sought.
        Returns:
          The path of the configuration file for the given installed transpiler. If multiple installed transpilers
          have the same transpiler name, either may be returned.
        Raises:
          ValueError: If there is no installed transpiler with the given transpiler name.
        """
        # Note: Because it's the transpiler name, have to hunt through the installed list rather get it directly.
        try:
            config = next(c for c in self._all_transpiler_configs() if c.name == transpiler_name)
        except StopIteration as e:
            raise ValueError(f"No such transpiler: {transpiler_name}") from e
        return config.path

    def transpiler_config_options(self, transpiler_name: str, source_dialect: str) -> Sequence[LSPConfigOptionV1]:
        """
        Query the additional configuration options that are available for a given transpiler and source dialect that it
        supports.

        Args:
          transpiler_name: The transpiler name of the transpiler that may provide options.
          source_dialect: The source dialect supported by the given for which options are sought.
        Returns:
          A sequence of configuration options, possibly empty, that are supported by the given transpiler for the
          specified source dialect.
        """
        config = self.all_transpiler_configs().get(transpiler_name, None)
        if not config:
            return []  # gracefully returns an empty list, since this can only happen during testing
        return config.options_for_dialect(source_dialect)

    def _all_transpiler_configs(self) -> Iterable[LSPConfig]:
        transpilers_path = self.transpilers_path()
        if transpilers_path.exists():
            all_files = os.listdir(transpilers_path)
            for file in all_files:
                config = self._transpiler_config(transpilers_path / file)
                if config:
                    yield config

    @classmethod
    def _transpiler_config(cls, path: Path) -> LSPConfig | None:
        if not path.is_dir() or not (path / "lib").is_dir():
            return None
        config_path = path / "lib" / "config.yml"
        if not config_path.is_file():
            return None
        try:
            return LSPConfig.load(config_path)
        except ValueError as e:
            logger.error(f"Could not load config: {path!s}", exc_info=e)
            return None
