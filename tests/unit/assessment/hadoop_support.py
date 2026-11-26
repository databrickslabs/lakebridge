"""Helpers shared by the Hadoop profiler tests."""

import json
import subprocess
import sys
from pathlib import Path

from databricks.labs.lakebridge.resources.assessments.hadoop import extract


def run_step_script(step: str, db_path: Path, creds: Path) -> None:
    """Run a Hadoop step script the way the profiler pipeline runs a python step.

    The pipeline starts ``python <script> --db-path <db> --credential-config-path <creds>``, reads the last line of the
    output as the step's JSON status, and fails the step when the status is not "success" or the exit code is not 0.
    """
    script = Path(extract.__file__).resolve().parent / f"{step}.py"
    result = subprocess.run(
        [sys.executable, str(script), "--db-path", str(db_path), "--credential-config-path", str(creds)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # one stream in the order it was written, as the pipeline reads it
        text=True,
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    try:
        status = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        status = {"status": "error", "message": "Could not parse script output as JSON, manually validate the logs."}
    if status.get("status") != "success":
        raise RuntimeError(f"Script reported error: {status.get('message', 'Unknown error')}")
    if result.returncode != 0:
        raise RuntimeError(f"Script execution failed with exit code {result.returncode}")
