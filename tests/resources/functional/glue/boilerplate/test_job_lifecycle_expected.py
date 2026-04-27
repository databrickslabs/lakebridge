from pyspark.context import SparkContext
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()

result_df = spark.read.table("processed.data")
