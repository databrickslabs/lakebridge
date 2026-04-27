from awsglue.context import GlueContext
from pyspark.context import SparkContext

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

result_df = spark.read.table("processed.orders")

glueContext.write_dynamic_frame.from_options(
    frame=result_df,
    connection_type="jdbc",
    connection_options={
        "url": "jdbc:postgresql://host:5432/mydb",
        "dbtable": "public.orders_out",
    },
)
