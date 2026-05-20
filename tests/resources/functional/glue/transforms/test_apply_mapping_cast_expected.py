from pyspark.context import SparkContext
from pyspark.sql import SparkSession
from pyspark.sql.functions import col
spark = SparkSession.builder.getOrCreate()

source_df = spark.read.table('raw.orders')

mapped_df = source_df.withColumnRenamed('id', 'order_id').withColumn('order_id', col('order_id').cast('bigint')).withColumnRenamed('amount', 'total_amount').withColumn('total_amount', col('total_amount').cast('double'))
