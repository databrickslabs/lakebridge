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
        ("id", "string", "order_id", "long"),
        ("amount", "string", "total_amount", "double"),
    ],
)
