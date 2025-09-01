from databricks.labs.lakebridge.__about__ import __version__
import platform

_product_name = "lakebridge"
_product_version = __version__
_user_agent = platform.system()
_stage_settings = {}
_output = {}

def set_stage_settings(settings: dict):
    global _stage_settings
    _stage_settings = settings

def set_output(output: dict):
    global _output
    _output = output

def clear_telemetry():
    global _stage_settings, _output
    _stage_settings = {}
    _output = {}

def propagate_telemetry() -> dict:
    pass
