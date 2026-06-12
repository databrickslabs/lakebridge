"""Registry of optional, source-specific pipeline configurators.

A configurator gets the loaded :class:`PipelineConfig`, the source credentials, and the
(optional) live extractor, and returns a possibly-adjusted pipeline. This lets a source
toggle steps at runtime based on credentials or probed capabilities without the
source-agnostic ``Profiler`` having to know about any specific platform — mirroring how
``_create_connector`` centralizes per-source connectors.
"""

from collections.abc import Callable, Mapping

from databricks.labs.lakebridge.assessments import teradata_pipeline
from databricks.labs.lakebridge.assessments.profiler_config import PipelineConfig
from databricks.labs.lakebridge.connections.database_manager import DatabaseManager

PipelineConfigurator = Callable[[PipelineConfig, Mapping[str, object], DatabaseManager | None], PipelineConfig]

PIPELINE_CONFIGURATORS: dict[str, PipelineConfigurator] = {
    "teradata": teradata_pipeline.configure_pipeline,
}
