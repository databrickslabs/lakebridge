from pyspark.context import SparkContext
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()

jdbc_df = spark.read.format('jdbc').option('url', 'jdbc:postgresql://host:5432/mydb').option('dbtable', 'public.transactions').load()
