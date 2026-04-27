"""Full realistic AWS Glue ETL job: read from catalog, apply mapping, write to S3."""
import sys
from awsglue.context import GlueContext
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from awsglue.job import Job
from pyspark.context import SparkContext

args = getResolvedOptions(sys.argv, ["JOB_NAME", "output_path"])

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

job = Job(glueContext)
job.init(args["JOB_NAME"], args)

# Read source data
orders_df = glueContext.create_dynamic_frame.from_catalog(
    database="source_db",
    table_name="orders",
)

# Apply column mapping and type casting
mapped_df = ApplyMapping.apply(
    frame=orders_df,
    mappings=[
        ("order_id", "string", "order_id", "long"),
        ("customer_id", "string", "customer_id", "long"),
        ("amount", "string", "amount", "double"),
    ],
)

# Write to S3
glueContext.write_dynamic_frame.from_options(
    frame=mapped_df,
    connection_type="s3",
    connection_options={"path": "s3://output-bucket/orders/", "partitionKeys": ["order_id"]},
    format="parquet",
)

job.commit()
