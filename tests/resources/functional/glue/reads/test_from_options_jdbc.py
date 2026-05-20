from awsglue.context import GlueContext
from pyspark.context import SparkContext

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

jdbc_df = glueContext.create_dynamic_frame.from_options(
    connection_type="jdbc",
    connection_options={
        "url": "jdbc:postgresql://host:5432/mydb",
        "dbtable": "public.transactions",
    },
)
