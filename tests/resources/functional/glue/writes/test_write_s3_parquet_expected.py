from pyspark.context import SparkContext
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()

output_df = spark.read.table("processed.orders")

output_df.write.format('parquet').save('s3://my-bucket/output/orders/')
