import pytest

from databricks.labs.lakebridge.intermediate.dag import DAG


@pytest.fixture(scope="module")
def dag():
    d = DAG()
    d.add_edge("parent_node", "child_node")
    return d


def test_add_node(dag):
    dag.add_node("test_node")
    assert "test_node" in dag.nodes


def test_add_edge(dag):
    dag.add_edge("edge_node", "node")
    assert "edge_node" in dag.nodes
    assert "node" in dag.nodes
    assert dag.nodes["node"].name in dag.nodes["edge_node"].children


def test_identify_immediate_parents(dag):
    parents = dag.identify_immediate_parents("child_node")
    assert parents == ["parent_node"]


def test_identify_immediate_children(dag):
    children = dag.identify_immediate_children("parent_node")
    assert children == ["child_node"]


def test_identify_root_tables(dag):
    root_tables = dag.identify_root_tables(0)
    assert "parent_node" in root_tables
