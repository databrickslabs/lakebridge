# Running Reconcile

This page covers how to run Lakebridge Reconcile via the CLI and via Databricks notebooks. For configuration options, see [Configuration Reference](/lakebridge/docs/reconcile/configuration.md).

***

## CLI Execution[​](#cli-execution "Direct link to CLI Execution")

After completing [setup](/lakebridge/docs/reconcile.md#setup), run reconciliation with:

```bash
databricks labs lakebridge reconcile

```

Results are written to the reconciliation dashboard deployed in your workspace.

***

## Notebook Execution[​](#notebook-execution "Direct link to Notebook Execution")

Use the notebook approach when you need fine-grained control over reconcile configuration, or when running from within a Databricks notebook workflow.

### Step 1: Install[​](#step-1-install "Direct link to Step 1: Install")

* PyPI

```python
%pip install databricks-labs-lakebridge
dbutils.library.restartPython()

```

### Step 2: Import[​](#step-2-import "Direct link to Step 2: Import")

```python
from databricks.sdk import WorkspaceClient

from databricks.labs.lakebridge.config import (
    ReconcileConfig,
    ReconcileMetadataConfig,
    TableRecon,
    SourceConnectionConfig,
    TargetConnectionConfig
)
from databricks.labs.lakebridge.reconcile.recon_config import (
    Table,
    ColumnMapping,
    ColumnThresholds,
    Transformation,
    JdbcReaderOptions,
    Aggregate,
    Filters
)
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService
from databricks.labs.lakebridge.reconcile.trigger_recon_aggregate_service import TriggerReconAggregateService
from databricks.labs.lakebridge.reconcile.exception import ReconciliationException

```

### Step 3: Configure ReconcileConfig[​](#step-3-configure-reconcileconfig "Direct link to Step 3: Configure ReconcileConfig")

#### **Before the actual example, some details about how to configure:**[​](#before-the-actual-example-some-details-about-how-to-configure "Direct link to before-the-actual-example-some-details-about-how-to-configure")

```python
class ReconcileConfig:
    report_type: str
    source: SourceConnectionConfig
    target: TargetConnectionConfig
    metadata_config: ReconcileMetadataConfig

```

Parameters:

* `report_type`: The type of report to be generated. Available report types are `schema`, `row`, `data` or `all`. For details check [here](/lakebridge/docs/reconcile/dataflow_example.md).

* `source`: The configuration for connecting to the source database to be reconciled.

  <!-- -->

  * `dialect`: The dialect of the source. Supported values: `snowflake`, `oracle`, `mssql`, `synapse`, `databricks`, `redshift`.
  * `catalog`: The source database/catalog name. catalog is used for consistency in naming
  * `schema`: The source schema name.
  * `uc_connection_name`: the connection name for the source as configured in workspace `Connections`. Not allowed for `databricks`

```python
@dataclass
class SourceConnectionConfig:
    dialect: str
    catalog: str
    schema: str
    uc_connection_name: str | None = None

```

* `target`: The specs of the target databricks catalog to be reconciled.

  <!-- -->

  * `catalog`: The target catalog name.
  * `schema`: The target schema name.

```python
@dataclass
class TargetConnectionConfig:
    catalog: str
    schema: str

```

* `metadata_config`: The metadata configuration. Reconcile uses this catalog & Schema on Databricks to store all the backend metadata details for reconciliation. expects a `ReconcileMetadataConfig` object.

  <!-- -->

  * `catalog`: The catalog name to store the metadata.
  * `schema`: The schema name to store the metadata.

```python
@dataclass
class ReconcileMetadataConfig:
    catalog: str = "lakebridge"
    schema: str = "reconcile"
    volume: str = "reconcile_volume"

```

If not set the default values will be used to store the metadata. The default resources are created during the installation of Lakebridge.

#### **Now, an Example of configuring the ReconcileConfig properties that you can copy into the notebook:**[​](#now-an-example-of-configuring-the-reconcileconfig-properties-that-you-can-copy-into-the-notebook "Direct link to now-an-example-of-configuring-the-reconcileconfig-properties-that-you-can-copy-into-the-notebook")

```python
reconcile_config = ReconcileConfig(
        report_type="all",
        source=SourceConnectionConfig(
            dialect="snowflake",
            catalog="source_sf_catalog",
            schema="source_sf_schema",
            uc_connection_name="source_connection_name"
        ),
        target=TargetConnectionConfig(
            catalog="target_databricks_catalog",
            schema="target_databricks_schema",
        ),
        metadata_config = ReconcileMetadataConfig(
              catalog = "lakebridge_metadata",
              schema= "reconcile"
         ),
    )

```

### Step 4: Configure TableRecon[​](#step-4-configure-tablerecon "Direct link to Step 4: Configure TableRecon")

```python
from databricks.labs.lakebridge.config import TableRecon
from databricks.labs.lakebridge.reconcile.recon_config import (
    Table,
    ColumnMapping,
    ColumnThresholds,
    TableThresholds,
    Transformation,
    JdbcReaderOptions,
    Aggregate,
    Filters
)

table_recon = TableRecon(
    tables=[
        Table(
            source_name="source_table_name",
            target_name="target_table_name",
            join_columns=["store_id", "account_id"],
            column_mapping=[
                ColumnMapping(source_name="dept_id", target_name="department_id"),
            ],
            column_thresholds=[
                ColumnThresholds(column_name="unit_price", upper_bound="-5", lower_bound="5", type="float")
            ],
            table_thresholds=[
                TableThresholds(lower_bound="0%", upper_bound="5%", model="mismatch")
            ],
            transformations=[
                Transformation(
                    column_name="inventory_units",
                    source="coalesce(cast(cast(inventory_units as decimal(38,10)) as string), '_null_recon_')",
                    target='coalesce(replace(cast(format_number(cast(inventory_units as decimal(38, 10)), 10) as string), ",", ""), "_null_recon_")'
                )
            ],
            jdbc_reader_options=JdbcReaderOptions(
                num_partitions=50,
                partition_column="lct_nbr",
                lower_bound="1",
                upper_bound="50000"
            ),
            filters=Filters(
                source="lower(dept_name)='finance'",
                target="lower(department_name)='finance'"
            )
        )
    ]
)

```

### Step 5: Run[​](#step-5-run "Direct link to Step 5: Run")

```python
from databricks.labs.lakebridge import __version__
from databricks.sdk import WorkspaceClient
from databricks.labs.lakebridge.reconcile.trigger_recon_service import TriggerReconService
from databricks.labs.lakebridge.reconcile.exception import ReconciliationException

ws = WorkspaceClient(product="lakebridge", product_version=__version__)

try:
    result = TriggerReconService.trigger_recon(
        ws=ws,
        spark=spark,
        table_recon=table_recon,
        reconcile_config=reconcile_config
    )
    print(result.recon_id)
    print(result)
except ReconciliationException as e:
    print(f"Failed: {e.reconcile_output.recon_id}")
    print(e)

```

### Visualization[​](#visualization "Direct link to Visualization")

After running, use the `recon_id` to drill into the results on the AI/BI Dashboard deployed in your workspace during installation.

note

Improved bulk automation tooling is in development. Check back for updates.
