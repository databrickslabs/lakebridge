import io
import logging
import os
import random
import string
from datetime import datetime, timezone
from pathlib import Path

from databricks.labs.blueprint.installation import RootJsonValue
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)


class SwitchRunner:
    """Runner for Switch LLM transpilation jobs."""

    def __init__(
        self,
        ws: WorkspaceClient,
    ):
        self._ws = ws

    def run(
        self,
        volume_input_path: str,
        output_ws_folder: str,
        source_tech: str,
        catalog: str,
        schema: str,
        foundation_model: str,
        job_id: int,
    ) -> RootJsonValue:
        """Trigger Switch job."""

        job_params = self._build_job_parameters(
            input_dir=volume_input_path,
            output_dir=output_ws_folder,
            source_tech=source_tech,
            catalog=catalog,
            schema=schema,
            foundation_model=foundation_model,
        )
        logger.info(f"Triggering Switch job with job_id: {job_id}")

        return self._run_job(job_id, job_params)

    def upload_to_volume(
        self,
        local_path: Path,
        catalog: str,
        schema: str,
        volume: str,
    ) -> str:
        """Upload local files to UC Volume with unique timestamped path."""
        now = datetime.now(timezone.utc)
        time_part = now.strftime("%Y%m%d%H%M%S")
        random_part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        volume_base_path = f"/Volumes/{catalog}/{schema}/{volume}"
        volume_input_path = f"{volume_base_path}/input-{time_part}-{random_part}"

        logger.info(f"Uploading {local_path} to {volume_input_path}...")

        # File upload
        if local_path.is_file():
            if local_path.name.startswith('.'):
                logger.debug(f"Skipping hidden file: {local_path}")
                return volume_input_path
            volume_file_path = f"{volume_input_path}/{local_path.name}"
            with open(local_path, 'rb') as f:
                content = f.read()
            self._ws.files.upload(file_path=volume_file_path, contents=io.BytesIO(content), overwrite=True)
            logger.debug(f"Uploaded: {local_path} -> {volume_file_path}")

        # Directory upload
        else:
            for root, dirs, files in os.walk(local_path):
                # remove hidden directories
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                # skip hidden files
                files = [f for f in files if not f.startswith('.')]
                for file in files:
                    local_file = Path(root) / file
                    relative_path = local_file.relative_to(local_path)
                    volume_file_path = f"{volume_input_path}/{relative_path}"

                    with open(local_file, 'rb') as f:
                        content = f.read()

                    self._ws.files.upload(file_path=volume_file_path, contents=io.BytesIO(content), overwrite=True)
                    logger.debug(f"Uploaded: {local_file} -> {volume_file_path}")

        logger.info(f"Upload complete: {volume_input_path}")
        return volume_input_path

    def _build_job_parameters(
        self,
        input_dir: str,
        output_dir: str,
        source_tech: str,
        catalog: str,
        schema: str,
        foundation_model: str,
        switch_options: dict | None = None,
    ) -> dict[str, str]:
        """Build Switch job parameters."""
        if switch_options is None:
            switch_options = {}
        return {
            "input_dir": input_dir,
            "output_dir": output_dir,
            "source_tech": source_tech,
            "catalog": catalog,
            "schema": schema,
            "foundation_model": foundation_model,
            **switch_options,
        }

    def _run_job(
        self,
        job_id: int,
        job_params: dict[str, str],
    ) -> RootJsonValue:
        """Trigger Switch job and return run information."""
        job_run = self._ws.jobs.run_now(job_id, job_parameters=job_params)

        if not job_run.run_id:
            raise SystemExit(f"Job {job_id} execution failed.")

        job_run_url = f"{self._ws.config.host}/jobs/{job_id}/runs/{job_run.run_id}"
        logger.info(f"Switch LLM transpilation job started: {job_run_url}")

        return [
            {
                "job_id": job_id,
                "run_id": job_run.run_id,
                "run_url": job_run_url,
            }
        ]
