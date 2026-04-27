from awsglue.context import GlueContext
from pyspark.context import SparkContext

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

customers_df = glueContext.create_dynamic_frame.from_catalog(database="sales_db", table_name="customers")
