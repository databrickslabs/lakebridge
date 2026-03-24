from __future__ import annotations

import shutil
from pathlib import Path

import yaml


def build_overridden_pipeline_config(
    *, test_resources: Path, tmp_path: Path, config_dir_name: str, extract_dir_name: str
) -> tuple[Path, Path]:
    """
    Create a copy of pipeline_config_absolute.yml with extract_folder/extract_source
    overridden to temporary paths and return (config_file_path, extract_folder).
    """
    config_dir = tmp_path / config_dir_name
    config_dir.mkdir()
    extract_folder = tmp_path / extract_dir_name
    config_file_src = test_resources / "assessments" / "pipeline_config_absolute.yml"
    config_file_dest = config_dir / config_file_src.name
    script_src = test_resources / "assessments" / "db_extract.py"
    script_dest = config_dir / script_src.name
    shutil.copy(script_src, script_dest)

    with open(config_file_src, "r", encoding="utf-8") as file:
        config_data = yaml.safe_load(file)
    config_data["extract_folder"] = str(extract_folder)
    for step in config_data["steps"]:
        step["extract_source"] = str(script_dest)
    with open(config_file_dest, "w", encoding="utf-8") as file:
        yaml.safe_dump(config_data, file)
    return config_file_dest, extract_folder
