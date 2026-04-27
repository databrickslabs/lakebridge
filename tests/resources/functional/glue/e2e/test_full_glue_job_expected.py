"""Full realistic AWS Glue ETL job: read from catalog, apply mapping, write to S3."""
import sys
from pyspark.context import SparkContext
from pyspark.sql import SparkSession
import argparse
from pyspark.sql.functions import col
_parser = argparse.ArgumentParser()
_parser.add_argument("--output_path")

args = vars(_parser.parse_args())
spark = SparkSession.builder.getOrCreate()

# Read source data
orders_df = spark.read.table('source_db.orders')

# Apply column mapping and type casting
mapped_df = orders_df.withColumn('order_id', col('order_id').cast('bigint')).withColumn('customer_id', col('customer_id').cast('bigint')).withColumn('amount', col('amount').cast('double'))

# Write to S3
mapped_df.write.format('parquet').partitionBy('order_id').save('s3://output-bucket/orders/')
