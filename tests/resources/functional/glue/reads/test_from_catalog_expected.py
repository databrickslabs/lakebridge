from pyspark.context import SparkContext
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()

customers_df = spark.read.table('sales_db.customers')
