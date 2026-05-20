from pyspark.context import SparkContext
from pyspark.sql import SparkSession
from pyspark.sql.functions import col
spark = SparkSession.builder.getOrCreate()

source_df = spark.read.table('raw.orders')

mapped_df = source_df.withColumn('order_id', col('order_id').cast('string')).withColumn('customer_name', col('customer_name').cast('string'))
