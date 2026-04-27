from pyspark.context import SparkContext
from pyspark.sql import SparkSession
from pyspark.sql.functions import col
spark = SparkSession.builder.getOrCreate()

src = spark.read.table('raw.events')

mapped = src.withColumn('id', col('id').cast('tinyint')).withColumn('counter', col('counter').cast('smallint')).withColumn('total', col('total').cast('bigint')).withColumn('active', col('active').cast('boolean')).withColumn('label', col('label').cast('string')).withColumn('amount', col('amount').cast('decimal(10,2)'))
