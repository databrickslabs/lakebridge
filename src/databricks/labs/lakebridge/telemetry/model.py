class TranspilerSettings:
    def __init__(self,
                 transpiler_name: str,
                 transpiler_version: str,
                 source_system: str,
                 target_system: str,
                 transpiler_flags: str):
        self._transpiler_name = transpiler_name
        self._transpiler_version = transpiler_version
        self._source_system = source_system
        self._target_system = target_system
        self._transpiler_flags = transpiler_flags

    def as_dict(self):
        return {
            "transpiler_name": self._transpiler_name,
            "transpiler_version": self._transpiler_version,
            "source_system": self._source_system,
            "target_system": self._target_system,
            "transpiler_flags": self._transpiler_flags
        }

class ReconcilerSettings:
    def __init__(self,
                 source_system,
                 target_system,
                 reconcile_report_type: str):
        self._source_system = source_system
        self._target_system = target_system
        self._reconcile_report_type = reconcile_report_type

    def as_dict(self):
        return {
            "source_system": self._source_system,
            "target_system": self._target_system,
            "reconcile_report_type": self._reconcile_report_type
        }
