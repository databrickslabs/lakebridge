from awsglue.context import GlueContext
from pyspark.context import SparkContext

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

output_df = spark.read.table("processed.orders")

glueContext.write_dynamic_frame.from_options(
    frame=output_df,
    connection_type="s3",
    connection_options={"path": "s3://my-bucket/output/orders/"},
    format="parquet",
)
