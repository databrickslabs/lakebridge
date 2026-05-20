from awsglue.context import GlueContext
from pyspark.context import SparkContext

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

raw_df = glueContext.create_dynamic_frame.from_options(
    connection_type="s3",
    connection_options={"path": "s3://my-bucket/raw/events/"},
    format="parquet",
)
