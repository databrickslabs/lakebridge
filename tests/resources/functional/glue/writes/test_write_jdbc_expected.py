from pyspark.context import SparkContext
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()

result_df = spark.read.table("processed.orders")

result_df.write.format('jdbc').option('url', 'jdbc:postgresql://host:5432/mydb').option('dbtable', 'public.orders_out').save()
