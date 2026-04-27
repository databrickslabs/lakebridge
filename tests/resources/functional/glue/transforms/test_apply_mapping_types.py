from awsglue.context import GlueContext
from awsglue.transforms import *
from pyspark.context import SparkContext

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

src = glueContext.create_dynamic_frame.from_catalog(database="raw", table_name="events")

mapped = ApplyMapping.apply(
    frame=src,
    mappings=[
        ("id",        "byte",    "id",        "byte"),
        ("counter",   "short",   "counter",   "short"),
        ("total",     "long",    "total",     "long"),
        ("active",    "bool",    "active",    "boolean"),
        ("label",     "char",    "label",     "string"),
        ("amount",    "decimal(10,2)", "amount", "decimal(10,2)"),
    ],
)
