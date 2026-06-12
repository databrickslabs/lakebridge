"""Teradata-specific pipeline configuration.

Kept out of the source-agnostic :class:`~databricks.labs.lakebridge.assessments.profiler.Profiler`
and wired in generically through ``PIPELINE_CONFIGURATORS`` (see
``pipeline_configurators.py``). Any source that needs to adjust its pipeline at
runtime based on credentials/capabilities can register a configurator the same way.
"""

import logging
from collections.abc import Mapping

from sqlalchemy.exc import SQLAlchemyError

from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig, Step
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager

logger = logging.getLogger(__name__)

# Non-DDL steps that read PDCR aggregates; deactivated when PDCR is unavailable/unwanted.
_PDCR_STEP_NAMES = {"td_pdcr_info_agg_extract", "td_pdcr_sp_exe_info_agg_extract"}
# DBQL-core fallback step, activated when PDCR is not used.
_DBQL_CORE_STEP = "td_dbql_core_info_extract"

# Lightweight relation/permission probes. If these are inaccessible or missing we
# fall back to the DBQL-core extract instead of failing the run.
_PDCR_PROBES = (
    "SELECT TOP 1 1 AS pdcr_probe FROM PDCRINFO.DBQLogTbl_Hst",
    "SELECT TOP 1 1 AS pdcr_probe FROM PDCRINFO.UserInfo",
)


def configure_pipeline(
    pipeline_config: PipelineConfig,
    connect_config: Mapping[str, object],
    extractor: DatabaseManager | None,
) -> PipelineConfig:
    """Return a pipeline config with PDCR/DBQL-core steps toggled for this environment.

    PDCR is the default. If the user opted in to PDCR but the relations are not
    accessible, we transparently fall back to the DBQL-core extract.
    """
    use_pdcr = _is_pdcr_requested(connect_config)
    if use_pdcr and extractor is not None and not _has_pdcr_access(extractor):
        use_pdcr = False
    return _apply_pdcr_choice(pipeline_config, use_pdcr=use_pdcr)


def _apply_pdcr_choice(pipeline_config: PipelineConfig, *, use_pdcr: bool) -> PipelineConfig:
    if use_pdcr:
        return pipeline_config

    updated_steps: list[Step] = []
    for step in pipeline_config.steps:
        if step.name in _PDCR_STEP_NAMES and step.type != "ddl":
            updated_steps.append(step.copy(flag="inactive"))
        elif step.name == _DBQL_CORE_STEP:
            updated_steps.append(step.copy(flag="active"))
        else:
            updated_steps.append(step)
    logger.info("Teradata profiler configured without PDCR; using DBQL core fallback extract.")
    return pipeline_config.copy(steps=updated_steps)


def _is_pdcr_requested(connect_config: Mapping[str, object] | None) -> bool:
    if not connect_config:
        return True
    profiler_config = connect_config.get("profiler")
    if isinstance(profiler_config, Mapping):
        return bool(profiler_config.get("use_pdcr", True))
    return True


def _has_pdcr_access(extractor: DatabaseManager) -> bool:
    # A failed probe is an expected, handled condition (PDCR not installed or no SELECT
    # grant), so we catch it locally rather than adding a swallow-everything API to the
    # shared DatabaseManager. DatabaseManager.fetch wraps connection errors as ConnectionError;
    # missing relations / permission errors surface as SQLAlchemyError subclasses.
    for query in _PDCR_PROBES:
        try:
            extractor.fetch(query)
        except (ConnectionError, SQLAlchemyError) as e:
            logger.info("PDCR relations are not accessible; using DBQL core fallback extract.")
            logger.debug(f"PDCR probe failed: {e}")
            return False
    return True
