from awsglue.context import GlueContext
from pyspark.context import SparkContext

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

events_df = spark.read.table("processed.events")

glueContext.write_dynamic_frame.from_options(
    frame=events_df,
    connection_type="s3",
    connection_options={"path": "s3://my-bucket/output/events/", "partitionKeys": ["year", "month"]},
    format="parquet",
)
