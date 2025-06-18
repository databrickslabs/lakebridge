import logging
import os
import sys
import subprocess

from databricks.labs.blueprint.entrypoint import find_project_root
from databricks.labs.blueprint.cli import App


def proxy_command(app: App, command: str):
    def fn(**_):
        proxy = JvmProxy()
        proxy.run()

    fn.__name__ = command
    fn.__doc__ = f"Proxy to run {command} in JVM"
    app.command(fn)


class JvmProxy:
    # TODO Refactor this class to use LSP protocol instead
    def __init__(self):
        self._root = find_project_root(__file__)
        databricks_logger = logging.getLogger("databricks")
        self._debug = databricks_logger.level == logging.DEBUG

    def _recompile(self):
        subprocess.run(
            ["mvn", "compile", "-f", f'{self._root}/pom.xml'],
            stdout=sys.stdout,
            stderr=sys.stderr,
            check=True,
        )

    def run(self):
        if self._debug:
            self._recompile()
        classpath = self._root / 'core/target/classpath.txt'
        classes = self._root / 'core/target/scala-2.12/classes'
        # TODO: use the os-specific path separator
        args = [
            "java",
            "--class-path",
            f'{classes.as_posix()}:{classpath.read_text()}',
            "com.databricks.labs.lakebridge.Main",
            sys.argv[1],
        ]
        with subprocess.Popen(
            args,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            env=os.environ.copy(),
            text=True,
        ) as process:
            return process.wait()
