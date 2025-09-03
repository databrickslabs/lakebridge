import logging
from pathlib import Path

from databricks.labs.blueprint.paths import read_text

from databricks.labs.lakebridge.helpers.file_utils import get_sql_file, is_sql_file
from databricks.labs.lakebridge.intermediate.dag import DAG

from databricks.labs.lakebridge.transpiler.sqlglot.sqlglot_engine import SqlglotEngine

logger = logging.getLogger(__name__)


class RootTableAnalyzer:

    def __init__(self, engine: SqlglotEngine, source_dialect: str, input_path: Path):
        self.engine = engine
        self.source_dialect = source_dialect
        self.input_path = input_path

    def generate_lineage_dag(self) -> DAG:
        dag = DAG()

        # when input is sql file then parse the file
        if is_sql_file(self.input_path):
            logger.debug(f"Generating Lineage file: {self.input_path}")
            sql_content = read_text(self.input_path)
            self._populate_dag(sql_content, self.input_path, dag)
            return dag  # return after processing the file

        # when the input is a directory
        for path in get_sql_file(self.input_path):
            logger.debug(f"Generating Lineage file: {path}")
            sql_content = read_text(path)
            self._populate_dag(sql_content, path, dag)

        return dag

    def _populate_dag(self, sql_content: str, path: Path, dag: DAG):
        for root_table, child in self.engine.analyse_table_lineage(self.source_dialect, sql_content, path):
            dag.add_node(child)
            dag.add_edge(root_table, child)
