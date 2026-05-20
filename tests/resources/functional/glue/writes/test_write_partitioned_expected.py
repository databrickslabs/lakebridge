from pyspark.context import SparkContext
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()

events_df = spark.read.table("processed.events")

events_df.write.format('parquet').partitionBy('year', 'month').save('s3://my-bucket/output/events/')
