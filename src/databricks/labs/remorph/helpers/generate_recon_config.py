import json
from dataclasses import asdict, is_dataclass

from databricks.labs.remorph.config import TableRecon


def generate_json_file(table_recon: TableRecon, file_name: str):
    def serialize(obj):
        """Helper function to handle optional complex objects"""
        if is_dataclass(obj):
            return asdict(obj)
        raise TypeError(f"Type {type(obj)} not serializable")

    table_recon_dict = asdict(table_recon)
    json_data = json.dumps(table_recon_dict, default=serialize, indent=4)

    try:
        with open(file_name, 'w') as json_file:
            json_file.write(json_data)
    except (TypeError, IOError) as e:
        print(f"Failed to generate JSON file: {e}")
        raise
