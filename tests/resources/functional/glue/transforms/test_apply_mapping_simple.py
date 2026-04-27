from awsglue.context import GlueContext
from awsglue.transforms import *
from pyspark.context import SparkContext

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

source_df = glueContext.create_dynamic_frame.from_catalog(database="raw", table_name="orders")

mapped_df = ApplyMapping.apply(
    frame=source_df,
    mappings=[
        ("order_id", "string", "order_id", "string"),
        ("customer_name", "string", "customer_name", "string"),
    ],
)
