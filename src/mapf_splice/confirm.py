from __future__ import annotations

from collections.abc import Iterable


def cyclic_components(
    edges: Iterable[tuple[str, str]],
) -> tuple[tuple[str, ...], ...]:
    """Tarjan SCCs of size >= 2 over waiting -> blocking edges, sorted."""
    graph: dict[str, set[str]] = {}
    for waiting, blocking in edges:
        graph.setdefault(waiting, set()).add(blocking)
        graph.setdefault(blocking, set())

    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def connect(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in sorted(graph[node]):
            if neighbor not in indexes:
                connect(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[neighbor])
        if lowlinks[node] == indexes[node]:
            members: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                members.append(member)
                if member == node:
                    break
            component = tuple(sorted(members))
            if len(component) >= 2:
                components.append(component)

    for node in sorted(graph):
        if node not in indexes:
            connect(node)
    return tuple(sorted(components))
