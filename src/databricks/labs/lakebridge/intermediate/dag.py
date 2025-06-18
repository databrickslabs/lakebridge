import logging

logger = logging.getLogger(__name__)


class Node:
    def __init__(self, name: str):
        self.name = name.lower()
        self.children: list[str] = []
        self.parents: list[str] = []

    def add_parent(self, node: str) -> None:
        self.parents.append(node)

    def add_child(self, node: str) -> None:
        self.children.append(node)

    def __repr__(self) -> str:
        return f"Node({self.name}, {self.children})"


class DAG:
    def __init__(self):
        self.nodes: dict[str, Node] = {}

    def add_node(self, node_name: str) -> None:
        if node_name not in self.nodes and node_name not in {None, "none"}:
            self.nodes[node_name.lower()] = Node(node_name.lower())

    def add_edge(self, parent_name: str, child_name: str) -> None:
        parent_name = parent_name.lower() if parent_name is not None else None
        child_name = child_name.lower() if child_name is not None else None
        logger.debug(f"Adding edge: {parent_name} -> {child_name}")
        if parent_name not in self.nodes:
            self.add_node(parent_name)
        if child_name not in self.nodes:
            self.add_node(child_name)

        if child_name is not None:
            self.nodes[parent_name].add_child(child_name)
            self.nodes[child_name].add_parent(parent_name)

    def identify_immediate_parents(self, table_name: str) -> list[str]:
        table_name = table_name.lower()  # convert to lower() case
        if table_name not in self.nodes:
            logger.debug(f"Table with the name {table_name} not found in the DAG")
            return []

        return list(self.nodes[table_name].parents)

    def identify_immediate_children(self, table_name: str) -> list[str]:
        table_name = table_name.lower()  # convert to lower() case
        if table_name not in self.nodes:
            logger.debug(f"Table with the name {table_name} not found in the DAG")
            return []

        return list(self.nodes[table_name].children)

    def _is_root_node(self, node_name: str) -> bool:
        return len(self.identify_immediate_parents(node_name)) == 0

    def walk_bfs(self, node: Node, level: int) -> set:
        tables_at_level = set()
        queue = [(node, 0)]  # The queue for the BFS. Each element is a tuple (node, level).
        while queue:
            current_node, node_level = queue.pop(0)

            if node_level == level:
                tables_at_level.add(current_node.name)
            elif node_level > level:
                break

            for child_name in self.identify_immediate_children(current_node.name):
                queue.append((self.nodes[child_name], node_level + 1))
        return tables_at_level

    def identify_root_tables(self, level: int) -> set:
        all_nodes = set(self.nodes.values())
        root_tables_at_level = set()

        for node in all_nodes:
            if self._is_root_node(node.name):
                root_tables_at_level.update(self.walk_bfs(node, level))

        return root_tables_at_level

    def __repr__(self) -> str:
        return str({node_name: str(node) for node_name, node in self.nodes.items()})
