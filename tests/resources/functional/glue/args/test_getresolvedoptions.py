import sys
from awsglue.utils import getResolvedOptions

args = getResolvedOptions(sys.argv, ["JOB_NAME", "source_bucket", "target_table"])

source = args["source_bucket"]
target = args["target_table"]
