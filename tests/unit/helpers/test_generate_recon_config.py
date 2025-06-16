from pathlib import Path

from databricks.labs.remorph.config import TableRecon
from databricks.labs.remorph.helpers.generate_recon_config import generate_json_file
from databricks.labs.remorph.reconcile.recon_config import Table, Aggregate, ColumnMapping


def test_generate_recon_config():
    table_recon = TableRecon(
        source_schema="default",
        target_catalog="users",
        target_schema="sriram_mohanty",
        tables=[
            Table(
                source_name="source_employee_table",
                target_name="target_employee_table",
                aggregates=[
                    Aggregate(agg_columns=["emp_id"], type="count"),
                    Aggregate(agg_columns=["salary"], type="min"),
                    Aggregate(agg_columns=["salary"], type="max"),
                ],
                column_mapping=[
                    ColumnMapping(source_name="emp_id", target_name="emp_id"),
                    ColumnMapping(source_name="salary", target_name="salary"),
                ],
                join_columns=["emp_id"],  # for recon type all join_columns is mandatory
            ),
            Table(source_name="source_dept_table", target_name="target_dept_table", join_columns=["dept_id"]),
        ],
    )
    output_file = "recon_config.json"
    generate_json_file(table_recon, output_file)
    assert Path(output_file).exists()
    Path(output_file).unlink()
