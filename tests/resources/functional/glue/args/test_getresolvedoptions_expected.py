import sys
import argparse
_parser = argparse.ArgumentParser()
_parser.add_argument("--source_bucket")
_parser.add_argument("--target_table")

args = vars(_parser.parse_args())

source = args["source_bucket"]
target = args["target_table"]
