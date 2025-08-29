import abc
import datetime as dt
import logging
import os
import re
import shutil
import subprocess
import sys
import venv
import xml.etree.ElementTree as ET
from json import dump, loads
from pathlib import Path
from shutil import rmtree
from typing import Any, Literal
from urllib import request
from urllib.error import HTTPError, URLError
from zipfile import ZipFile

from databricks.labs.lakebridge.transpiler.repository import TranspilerRepository

logger = logging.getLogger(__name__)


class _PathBackup:
    """A context manager to preserve a path before performing an operation, and optionally restore it afterwards."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._backup_path: Path | None = None
        self._finished = False

    def __enter__(self) -> "_PathBackup":
        self.start()
        return self

    def start(self) -> None:
        """Start the backup process by creating a backup of the path, if it already exists."""
        backup_path = self._path.with_name(f"{self._path.name}-saved")
        if backup_path.exists():
            logger.debug(f"Existing backup found, removing: {backup_path}")
            rmtree(backup_path)
        if self._path.exists():
            logger.debug(f"Backing up existing path: {self._path} -> {backup_path}")
            os.rename(self._path, backup_path)
            self._backup_path = backup_path
        else:
            self._backup_path = None

    def rollback(self) -> None:
        """Rollback the operation by restoring the backup path, if it exists."""
        assert not self._finished, "Can only rollback/commit once."
        logger.debug(f"Removing path: {self._path}")
        rmtree(self._path)
        if self._backup_path is not None:
            logger.debug(f"Restoring previous path: {self._backup_path} -> {self._path}")
            os.rename(self._backup_path, self._path)
            self._backup_path = None
        self._finished = True

    def commit(self) -> None:
        """Commit the operation by removing the backup path, if it exists."""
        assert not self._finished, "Can only rollback/commit once."
        if self._backup_path is not None:
            logger.debug(f"Removing backup path: {self._backup_path}")
            rmtree(self._backup_path)
            self._backup_path = None
        self._finished = True

    def __exit__(self, exc_type, exc_val, exc_tb) -> Literal[False]:
        if not self._finished:
            # Automatically commit or rollback based on whether an exception is underway.
            if exc_val is None:
                self.commit()
            else:
                self.rollback()
        return False  # Do not suppress any exception underway


class ArtifactInstaller(abc.ABC):

    # TODO: Remove these properties when post-install is removed.
    _install_path: Path
    """The path where the transpiler is being installed, once this starts."""

    def __init__(self, repository: TranspilerRepository, product_name: str) -> None:
        self._repository = repository
        self._product_name = product_name

    _version_pattern = re.compile(r"[_-](\d+(?:[.\-_]\w*\d+)+)")

    @classmethod
    def get_local_artifact_version(cls, artifact: Path) -> str | None:
        # TODO: Get the version from the metadata inside the artifact rather than relying on the filename.
        match = cls._version_pattern.search(artifact.stem)
        if not match:
            return None
        group = match.group(0)
        if not group:
            return None
        # TODO: Update the regex to take care of these trimming scenarios.
        if group.startswith('-'):
            group = group[1:]
        if group.endswith("-py3"):
            group = group[:-4]
        return group

    @classmethod
    def _store_product_state(cls, product_path: Path, version: str) -> None:
        state_path = product_path / "state"
        state_path.mkdir()
        version_data = {"version": f"v{version}", "date": dt.datetime.now(dt.timezone.utc).isoformat()}
        version_path = state_path / "version.json"
        with version_path.open("w", encoding="utf-8") as f:
            dump(version_data, f)
            f.write("\n")

    def _install_version_with_backup(self, version: str) -> Path | None:
        """Install a specific version of the transpiler, with backup handling."""
        logger.info(f"Installing Databricks {self._product_name} transpiler (v{version})")
        product_path = self._repository.transpilers_path() / self._product_name
        with _PathBackup(product_path) as backup:
            self._install_path = product_path / "lib"
            self._install_path.mkdir(parents=True, exist_ok=True)
            try:
                result = self._install_version(version)
            except (subprocess.CalledProcessError, KeyError, ValueError) as e:
                # Warning: if you end up here under the IntelliJ/PyCharm debugger, it can be because the debugger is
                # trying to inject itself into the subprocess. Try disabling:
                #   Settings | Build, Execution, Deployment | Python Debugger | Attach to subprocess automatically while debugging
                # Note: Subprocess output is not captured, and should already be visible in the console.
                logger.error(f"Failed to install {self._product_name} transpiler (v{version})", exc_info=e)
                result = False

            if result:
                logger.info(f"Successfully installed {self._product_name} transpiler (v{version})")
                self._store_product_state(product_path=product_path, version=version)
                backup.commit()
                return product_path
            backup.rollback()
        return None

    @abc.abstractmethod
    def _install_version(self, version: str) -> bool:
        """Install a specific version of the transpiler, returning True if successful."""


class WheelInstaller(ArtifactInstaller):

    _venv_exec_cmd: Path
    """Once created, the command to run the virtual environment's Python executable."""

    _site_packages: Path
    """Once created, the path to the site-packages directory in the virtual environment."""

    @classmethod
    def get_latest_artifact_version_from_pypi(cls, product_name: str) -> str | None:
        try:
            with request.urlopen(f"https://pypi.org/pypi/{product_name}/json") as server:
                text: bytes = server.read()
            data: dict[str, Any] = loads(text)
            return data.get("info", {}).get('version', None)
        except HTTPError as e:
            logger.error(f"Error while fetching PyPI metadata: {product_name}", exc_info=e)
            return None

    def __init__(
        self,
        repository: TranspilerRepository,
        product_name: str,
        pypi_name: str,
        artifact: Path | None = None,
    ) -> None:
        super().__init__(repository, product_name)
        self._pypi_name = pypi_name
        self._artifact = artifact

    def install(self) -> Path | None:
        return self._install_checking_versions()

    def _install_checking_versions(self) -> Path | None:
        latest_version = (
            self.get_local_artifact_version(self._artifact)
            if self._artifact
            else self.get_latest_artifact_version_from_pypi(self._pypi_name)
        )
        if latest_version is None:
            logger.warning(f"Could not determine the latest version of {self._pypi_name}")
            logger.error(f"Failed to install transpiler: {self._product_name}")
            return None
        installed_version = self._repository.get_installed_version(self._product_name)
        if installed_version == latest_version:
            logger.info(f"{self._pypi_name} v{latest_version} already installed")
            return None
        return self._install_version_with_backup(latest_version)

    def _install_version(self, version: str) -> bool:
        self._create_venv()
        self._install_with_pip()
        self._copy_lsp_resources()
        return self._post_install() is not None

    def _create_venv(self) -> None:
        venv_path = self._install_path / ".venv"
        # Sadly, some platform-specific variations need to be dealt with:
        #   - Windows venvs do not use symlinks, but rather copies, when populating the venv.
        #   - The library path is different.
        if use_symlinks := sys.platform != "win32":
            major, minor = sys.version_info[:2]
            lib_path = venv_path / "lib" / f"python{major}.{minor}" / "site-packages"
        else:
            lib_path = venv_path / "Lib" / "site-packages"
        builder = venv.EnvBuilder(with_pip=True, prompt=f"{self._product_name}", symlinks=use_symlinks)
        builder.create(venv_path)
        context = builder.ensure_directories(venv_path)
        logger.debug(f"Created virtual environment with context: {context}")
        self._venv_exec_cmd = context.env_exec_cmd
        self._site_packages = lib_path

    def _install_with_pip(self) -> None:
        # Based on: https://pip.pypa.io/en/stable/user_guide/#using-pip-from-your-program
        # (But with venv_exec_cmd instead of sys.executable, so that we use the venv's pip.)
        to_install: Path | str = self._artifact if self._artifact is not None else self._pypi_name
        command: list[Path | str] = [
            self._venv_exec_cmd,
            "-m",
            "pip",
            "--disable-pip-version-check",
            "install",
            to_install,
        ]
        result = subprocess.run(command, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, check=False)
        result.check_returncode()

    def _copy_lsp_resources(self):
        lsp = self._site_packages / "lsp"
        if not lsp.exists():
            raise ValueError("Installed transpiler is missing a 'lsp' folder")
        shutil.copytree(lsp, self._install_path, dirs_exist_ok=True)

    def _post_install(self) -> Path | None:
        config = self._install_path / "config.yml"
        if not config.exists():
            raise ValueError("Installed transpiler is missing a 'config.yml' file in its 'lsp' folder")
        install_ext = "ps1" if sys.platform == "win32" else "sh"
        install_script = f"installer.{install_ext}"
        installer_path = self._install_path / install_script
        if installer_path.exists():
            self._run_custom_installer(installer_path)
        return self._install_path

    def _run_custom_installer(self, installer_path: Path) -> None:
        args = [installer_path]
        subprocess.run(args, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, cwd=self._install_path, check=True)


class MavenInstaller(ArtifactInstaller):
    # Maven Central, base URL.
    _maven_central_repo: str = "https://repo.maven.apache.org/maven2/"

    @classmethod
    def _artifact_base_url(cls, group_id: str, artifact_id: str) -> str:
        """Construct the base URL for a Maven artifact."""
        # Reference: https://maven.apache.org/repositories/layout.html
        group_path = group_id.replace(".", "/")
        return f"{cls._maven_central_repo}{group_path}/{artifact_id}/"

    @classmethod
    def artifact_metadata_url(cls, group_id: str, artifact_id: str) -> str:
        """Get the metadata URL for a Maven artifact."""
        # TODO: Unit test this method.
        return f"{cls._artifact_base_url(group_id, artifact_id)}maven-metadata.xml"

    @classmethod
    def artifact_url(
        cls, group_id: str, artifact_id: str, version: str, classifier: str | None = None, extension: str = "jar"
    ) -> str:
        """Get the URL for a versioned Maven artifact."""
        # TODO: Unit test this method, including classifier and extension.
        _classifier = f"-{classifier}" if classifier else ""
        artifact_base_url = cls._artifact_base_url(group_id, artifact_id)
        return f"{artifact_base_url}{version}/{artifact_id}-{version}{_classifier}.{extension}"

    @classmethod
    def get_current_maven_artifact_version(cls, group_id: str, artifact_id: str) -> str | None:
        url = cls.artifact_metadata_url(group_id, artifact_id)
        try:
            with request.urlopen(url) as server:
                text = server.read()
        except HTTPError as e:
            logger.error(f"Error while fetching maven metadata: {group_id}:{artifact_id}", exc_info=e)
            return None
        logger.debug(f"Maven metadata for {group_id}:{artifact_id}: {text}")
        return cls._extract_latest_release_version(text)

    @classmethod
    def _extract_latest_release_version(cls, maven_metadata: str) -> str | None:
        """Extract the latest release version from Maven metadata."""
        # Reference: https://maven.apache.org/repositories/metadata.html#The_A_Level_Metadata
        # TODO: Unit test this method, to verify the sequence of things it checks for.
        root = ET.fromstring(maven_metadata)
        for label in ("release", "latest"):
            version = root.findtext(f"./versioning/{label}")
            if version is not None:
                return version
        return root.findtext("./versioning/versions/version[last()]")

    @classmethod
    def download_artifact_from_maven(
        cls,
        group_id: str,
        artifact_id: str,
        version: str,
        target: Path,
        classifier: str | None = None,
        extension: str = "jar",
    ) -> bool:
        if target.exists():
            logger.warning(f"Skipping download of {group_id}:{artifact_id}:{version}; target already exists: {target}")
            return True
        url = cls.artifact_url(group_id, artifact_id, version, classifier, extension)
        try:
            path, _ = request.urlretrieve(url)
            logger.debug(f"Downloaded maven artefact from {url} to {path}")
        except URLError as e:
            logger.error(f"Unable to download maven artefact: {group_id}:{artifact_id}:{version}", exc_info=e)
            return False
        logger.debug(f"Moving {path} to {target}")
        shutil.move(path, target)
        logger.info(f"Successfully installed: {group_id}:{artifact_id}:{version}")
        return True

    def __init__(
        self,
        repository: TranspilerRepository,
        product_name: str,
        group_id: str,
        artifact_id: str,
        artifact: Path | None = None,
    ) -> None:
        super().__init__(repository, product_name)
        self._group_id = group_id
        self._artifact_id = artifact_id
        self._artifact = artifact

    def install(self) -> Path | None:
        return self._install_checking_versions()

    def _install_checking_versions(self) -> Path | None:
        if self._artifact:
            latest_version = self.get_local_artifact_version(self._artifact)
        else:
            latest_version = self.get_current_maven_artifact_version(self._group_id, self._artifact_id)
        if latest_version is None:
            logger.warning(f"Could not determine the latest version of Databricks {self._product_name} transpiler")
            logger.error("Failed to install transpiler: Databricks {self._product_name} transpiler")
            return None
        installed_version = self._repository.get_installed_version(self._product_name)
        if installed_version == latest_version:
            logger.info(f"Databricks {self._product_name} transpiler v{latest_version} already installed")
            return None
        return self._install_version_with_backup(latest_version)

    def _install_version(self, version: str) -> bool:
        jar_file_path = self._install_path / f"{self._artifact_id}.jar"
        if self._artifact:
            logger.debug(f"Copying: {self._artifact} -> {jar_file_path}")
            shutil.copyfile(self._artifact, jar_file_path)
        elif not self.download_artifact_from_maven(self._group_id, self._artifact_id, version, jar_file_path):
            logger.error(f"Failed to install Databricks {self._product_name} transpiler (v{version})")
            return False
        self._copy_lsp_config(jar_file_path)
        return True

    def _copy_lsp_config(self, jar_file_path: Path) -> None:
        with ZipFile(jar_file_path) as zip_file:
            zip_file.extract("lsp/config.yml", self._install_path)
        shutil.move(self._install_path / "lsp" / "config.yml", self._install_path / "config.yml")
        os.rmdir(self._install_path / "lsp")


class TranspilerInstaller(abc.ABC):
    def __init__(self, transpiler_repository: TranspilerRepository) -> None:
        self._transpiler_repository = transpiler_repository

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """The name of this transpiler, as noted in its internal configuration."""

    @abc.abstractmethod
    def can_install(self, artifact: Path) -> bool:
        """Check whether the given path is an artifact that can be installed by this installed."""

    @abc.abstractmethod
    def install(self, artifact: Path | None = None) -> bool:
        """Install or upgrade a transpiler.

        This method is responsible for installing a transpiler, including obtaining any necessary online artifacts.

        Args:
            artifact: An optional local path for the transpiler artifact, if it should be used instead of the online
                artifact.
        Returns:
            True if the transpiler was installed or updated, or False if the transpiler was already up-to-date.
        """


class BladebridgeInstaller(TranspilerInstaller):
    @property
    def name(self) -> str:
        return "Bladebridge"

    def can_install(self, artifact: Path) -> bool:
        return "databricks_bb_plugin" in artifact.name and artifact.suffix == ".whl"

    def install(self, artifact: Path | None = None) -> bool:
        local_name = "bladebridge"
        pypi_name = "databricks-bb-plugin"
        wheel_installer = WheelInstaller(self._transpiler_repository, local_name, pypi_name, artifact)
        return wheel_installer.install() is not None


class MorpheusInstaller(TranspilerInstaller):
    @property
    def name(self) -> str:
        return "Morpheus"

    def can_install(self, artifact: Path) -> bool:
        return "databricks-morph-plugin" in artifact.name and artifact.suffix == ".jar"

    def install(self, artifact: Path | None = None) -> bool:
        if not self.is_java_version_okay():
            logger.error(
                "The morpheus transpiler requires Java 11 or above. Please install Java and re-run 'install-transpile'."
            )
            return False
        product_name = "databricks-morph-plugin"
        group_id = "com.databricks.labs"
        artifact_id = product_name
        maven_installer = MavenInstaller(self._transpiler_repository, product_name, group_id, artifact_id, artifact)
        return maven_installer.install() is not None

    @classmethod
    def is_java_version_okay(cls) -> bool:
        detected_java = cls.find_java()
        match detected_java:
            case None:
                logger.warning("No Java executable found in the system PATH.")
                return False
            case (java_executable, None):
                logger.warning(f"Java found, but could not determine the version: {java_executable}.")
                return False
            case (java_executable, bytes(raw_version)):
                logger.warning(f"Java found ({java_executable}), but could not parse the version:\n{raw_version}")
                return False
            case (java_executable, tuple(old_version)) if old_version < (11, 0, 0, 0):
                version_str = ".".join(str(v) for v in old_version)
                logger.warning(f"Java found ({java_executable}), but version {version_str} is too old.")
                return False
            case _:
                return True

    @classmethod
    def find_java(cls) -> tuple[Path, tuple[int, int, int, int] | bytes | None] | None:
        """Locate Java and return its version, as reported by `java -version`.

        The java executable is currently located by searching the system PATH. Its version is parsed from the output of
        the `java -version` command, which has been standardized since Java 10.

        Returns:
            a tuple of its path and the version as a tuple of integers (feature, interim, update, patch), if the java
            executable could be located. If the version cannot be parsed, instead the raw version information is
            returned, or `None` as a last resort. When no java executable is found, `None` is returned instead of a
            tuple.
        """
        # Platform-independent way to reliably locate the java executable.
        # Reference: https://docs.python.org/3.10/library/subprocess.html#popen-constructor
        java_executable = shutil.which("java")
        if java_executable is None:
            return None
        java_executable_path = Path(java_executable)
        logger.debug(f"Using java executable: {java_executable_path!r}")
        try:
            completed = subprocess.run(
                [str(java_executable_path), "-version"], shell=False, capture_output=True, check=True
            )
        except subprocess.CalledProcessError as e:
            logger.debug(
                f"Failed to run {e.args!r} (exit-code={e.returncode}, stdout={e.stdout!r}, stderr={e.stderr!r})",
                exc_info=e,
            )
            return java_executable_path, None
        # It might not be ascii, but the bits we care about are so this will never fail.
        raw_output = completed.stderr
        java_version_output = raw_output.decode("ascii", errors="ignore")
        java_version = cls._parse_java_version(java_version_output)
        if java_version is None:
            return java_executable_path, raw_output.strip()
        logger.debug(f"Detected java version: {java_version}")
        return java_executable_path, java_version

    # Pattern to match a Java version string, compiled at import time to ensure it's valid.
    # Ref: https://docs.oracle.com/en/java/javase/11/install/version-string-format.html
    _java_version_pattern = re.compile(
        r' version "(?P<feature>\d+)(?:\.(?P<interim>\d+)(?:\.(?P<update>\d+)(?:\.(?P<patch>\d+))?)?)?"'
    )

    @classmethod
    def _parse_java_version(cls, version: str) -> tuple[int, int, int, int] | None:
        """Locate and parse the Java version in the output of `java -version`."""
        # Output looks like this:
        #   openjdk version "24.0.1" 2025-04-15
        #   OpenJDK Runtime Environment Temurin-24.0.1+9 (build 24.0.1+9)
        #   OpenJDK 64-Bit Server VM Temurin-24.0.1+9 (build 24.0.1+9, mixed mode)
        match = cls._java_version_pattern.search(version)
        if not match:
            logger.debug(f"Could not parse java version: {version!r}")
            return None
        feature = int(match["feature"])
        interim = int(match["interim"] or 0)
        update = int(match["update"] or 0)
        patch = int(match["patch"] or 0)
        return feature, interim, update, patch
