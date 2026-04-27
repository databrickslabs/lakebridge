from pyspark.context import SparkContext
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()

raw_df = spark.read.format('parquet').load('s3://my-bucket/raw/events/')
