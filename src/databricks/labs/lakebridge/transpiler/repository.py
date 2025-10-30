from __future__ import annotations

from collections.abc import Generator, Iterable, Mapping, Sequence, Set
from dataclasses import dataclass
from json import loads
from typing import Any
from pathlib import Path
import logging

from databricks.labs.lakebridge.config import LSPConfigOptionV1
from databricks.labs.lakebridge.transpiler.lsp.lsp_engine import LSPConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class TranspilerInfo:
    transpiler_name: str
    version: str | None
    configuration_path: Path
    dialects: Mapping[str, Sequence[LSPConfigOptionV1]]


class TranspilerRepository:
    """
    Repository for managing the installed transpilers in the user's home directory.

    The default repository for a user is always located under ~/.databricks/labs, and can be obtained
    via the `TranspilerRepository.user_home()` method.
    """

    #
    # Transpilers currently have different identifiers, for historical reasons:
    #
    #  - transpiler_id: the unique identifier of the transpiler according to this project, assigned within the `installer`
    #        module and used as the name of the directory into which the transpiler is installed within a repository.
    #  - transpiler_name: the name of the transpiler according to its own metadata, found in the configuration file
    #       bundled within each transpiler as distributed.
    #
    # Note: multiple installed transpilers might have the same transpiler name, but a transpiler id is unique to a single
    # installed transpiler.
    #
    #  Known identifiers at the moment:
    #
    #   - Morpheus:     transpiler_id = databricks-morph-plugin,  transpiler_name = Morpheus
    #   - BladeBridge:  transpiler_id = bladebridge,              transpiler_name = Bladebridge
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

    @classmethod
    def _parse_version_file(cls, transpiler_path: Path) -> str | None:
        """
        Obtain the version of an installed transpiler.

        Args:
          transpiler_path: The path of the transpiler whose version is sought.
        Returns:
          The version of the transpiler if it is installed, or None otherwise.
        """
        current_version_path = transpiler_path / "state" / "version.json"
        try:
            text = current_version_path.read_text("utf-8")
        except FileNotFoundError:
            return None
        data: dict[str, Any] = loads(text)
        version: str | None = data.get("version", None)
        if not version or not version.startswith("v"):
            return None
        return version[1:]

    def get_installed_version(self, transpiler_id: str) -> str | None:
        # Warning: transpiler_id here (eg. 'morpheus') and transpiler_name elsewhere (eg. Morpheus) are not the same!
        transpiler_path = self.transpilers_path() / transpiler_id
        return self._parse_version_file(transpiler_path)

    def get_installed_version_given_config_path(self, transpiler_config_path: Path) -> str | None:
        transpiler_path = transpiler_config_path.parent.parent
        return self._parse_version_file(transpiler_path)

    def all_transpiler_configs(self) -> Mapping[str, LSPConfig]:
        """Obtain all installed transpile configurations.

        Returns:
          A mapping of configurations, keyed by their ids.
        """
        return {path.name: config for path, config in self._all_transpiler_configs()}

    def all_transpiler_names(self) -> Set[str]:
        """Query the set of transpiler names for all installed transpilers."""
        all_configs = self._all_transpiler_configs()
        return frozenset(config.name for _, config in all_configs)

    def _transpiler_locations(self) -> Generator[Path, None, None]:
        transpilers_path = self.transpilers_path()
        try:
            # Treat the first entry specially: failure here is different from failure once underway.
            iterator = transpilers_path.iterdir()
            yield next(iterator)
        except StopIteration:
            # Harmless: no transpilers installed.
            return
        except OSError as e:
            # Also generally non-fatal, on the first entry: normally means there's no installation.
            logger.debug("Unable to list installed transpilers", exc_info=e)
            return
        # After the first entry, continue yielding but any errors need to propagate: something is wrong.
        yield from iterator

    def installed_transpilers(self) -> Mapping[str, TranspilerInfo]:
        """Query the set of installed transpilers and their metadata."""
        return {
            path.name: TranspilerInfo(
                transpiler_name=config.name,
                version=self.get_installed_version(path.name),
                configuration_path=config.path,
                dialects={dialect: config.options_for_dialect(dialect) for dialect in config.remorph.dialects},
            )
            for path, config in self._all_transpiler_configs()
        }

    def all_dialects(self) -> Set[str]:
        """Query the set of dialects for all installed transpilers."""
        all_dialects: set[str] = set()
        for _, config in self._all_transpiler_configs():
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

    def _find_transpile_config(self, transpiler_name: str) -> LSPConfig | None:
        try:
            return next(c for _, c in self._all_transpiler_configs() if c.name == transpiler_name)
        except StopIteration:
            return None

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
        config = self._find_transpile_config(transpiler_name)
        if config is None:
            raise ValueError(f"No such transpiler: {transpiler_name}")
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
        config = self._find_transpile_config(transpiler_name)
        if config is None:
            return []  # gracefully returns an empty list, since this can only happen during testing
        return config.options_for_dialect(source_dialect)

    def _all_transpiler_configs(self) -> Iterable[tuple[Path, LSPConfig]]:
        for path in self._transpiler_locations():
            config = self._transpiler_config(path)
            if config:
                yield path, config

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
