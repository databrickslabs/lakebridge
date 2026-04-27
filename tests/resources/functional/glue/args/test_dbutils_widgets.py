import sys
from awsglue.utils import getResolvedOptions

args = getResolvedOptions(sys.argv, ["JOB_NAME", "source_db", "output_path"])

source = args["source_db"]
output = args["output_path"]
