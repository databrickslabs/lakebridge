import sys
dbutils.widgets.text('source_db', "")
dbutils.widgets.text('output_path', "")

args = {k: dbutils.widgets.get(k) for k in ['source_db', 'output_path']}

source = args["source_db"]
output = args["output_path"]
