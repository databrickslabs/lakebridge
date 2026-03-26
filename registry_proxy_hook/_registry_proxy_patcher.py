"""Patch lakebridge to use Databricks internal proxies (Maven Central + PyPI are blocked).

This module is loaded via a .pth file at Python startup. It overrides builtins.__import__
so that when lakebridge's installers module is imported, the Maven and PyPI URLs are
replaced with the Databricks internal proxy URLs.

This approach survives `databricks labs upgrade lakebridge` which reinstalls the package
and overwrites any direct source patches.

To install, copy both files into the lakebridge venv's site-packages:
    VENV=~/.databricks/labs/lakebridge/state/venv/lib/python3.13/site-packages
    cp _registry_proxy_patcher.py "$VENV/"
    cp registry_proxy.pth "$VENV/"
"""
import builtins

_original_import = builtins.__import__


def _patched_import(name, *args, **kwargs):
    mod = _original_import(name, *args, **kwargs)
    if name == "databricks.labs.lakebridge.transpiler.installers" or (
        hasattr(mod, "MavenInstaller") and hasattr(mod, "WheelInstaller")
        and getattr(mod, "__name__", "") == "databricks.labs.lakebridge.transpiler.installers"
    ):
        _apply_patches(mod)
    return mod


def _apply_patches(mod):
    if getattr(mod, "_proxy_patched", False):
        return
    mod.MavenInstaller._maven_central_repo = "https://maven-proxy.dev.databricks.com/"

    import requests as req
    from requests.exceptions import RequestException

    @classmethod
    def _proxy_pypi(cls, artifact_id):
        url = f"https://pypi-proxy.dev.databricks.com/pypi/{artifact_id}/json"
        try:
            response = req.get(url, timeout=60)
            response.raise_for_status()
            data = response.json()
        except RequestException as e:
            mod.logger.error(f"Error while fetching PyPI metadata: {artifact_id}", exc_info=e)
            return None
        match data:
            case {"info": {"version": str(version), **_ignored}, **_also_ignored}:
                return version
            case _:
                return None

    mod.WheelInstaller.get_latest_artifact_version_from_pypi = _proxy_pypi
    mod._proxy_patched = True


builtins.__import__ = _patched_import
