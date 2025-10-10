from typing import cast

from databricks.labs.blueprint.installation import JsonList, JsonObject, RootJsonValue

from databricks.labs.lakebridge.config import LSPConfigOptionV1, LSPPromptMethod
from databricks.labs.lakebridge.transpiler.repository import TranspilerInfo, TranspilerRepository


class TranspilersDescription:
    def __init__(self, transpiler_repository: TranspilerRepository) -> None:
        self._transpiler_repository = transpiler_repository

    @classmethod
    def transpiler_as_json(cls, transpiler: TranspilerInfo) -> JsonObject:
        return {
            "name": transpiler.transpiler_name,
            "config-path": str(transpiler.configuration_path),
            "versions": {
                "installed": transpiler.version,
                # TODO: Determine the lastest version available, if possible.
                "latest": None,
            },
            "supported-dialects": {
                dialect: {"options": [cls.dialect_options_as_json(option) for option in options]}
                for dialect, options in transpiler.dialects.items()
            },
        }

    @classmethod
    def dialect_options_as_json(cls, option: LSPConfigOptionV1) -> JsonObject:
        description: JsonObject = {
            "flag": option.flag,
            "method": option.method.name,
        }
        if option.method != LSPPromptMethod.FORCE:
            description["prompt"] = option.prompt
        if option.method == LSPPromptMethod.CHOICE:
            description["choices"] = cast(JsonList, option.choices)
        if option.default is not None:
            description["default"] = option.default
        return description

    def as_json(self) -> RootJsonValue:
        """Obtain a JSON description of the installed transpilers.

        Information includes:
         - Plugins and their versions.
         - Dialects supported by each plugin.
         - The options that each dialect supports.
        """
        transpiler_repository = self._transpiler_repository
        installed_transpilers = transpiler_repository.installed_transpilers()
        all_dialects = cast(JsonList, sorted(list(transpiler_repository.all_dialects())))
        return {
            "installed-transpilers": [
                self.transpiler_as_json(info) for transpiler_id, info in sorted(installed_transpilers.items())
            ],
            "available-dialects": all_dialects,
        }
