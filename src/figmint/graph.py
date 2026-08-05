"""The provenance record as a picture.

`figmint.toml` is already a DAG: every artifact names its inputs, and some of
those inputs are themselves artifacts. Drawing it needs nothing but the record —
no pipeline to interrogate, no static analysis, no second source of truth that
could disagree with the freshness check printed beside it.

Rendered as Mermaid because MyST draws that natively, so a document gets the
diagram without figmint shipping a line of JavaScript.

What the shapes mean is the whole design. A reader should be able to tell, at a
glance, which nodes are *recorded* (and therefore checkable), which are raw
inputs nothing produced, and which are stale — and the last of those has to be
visible without reading a legend, because it is the one that matters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .status import ArtifactStatus, State, check_all
from .store import Store

#: Node shapes, by what the node *is*. Mermaid's vocabulary is small, so this
#: groups rather than distinguishing everything.
SHAPES = {
    #: A recorded artifact: something figmint watched being made.
    "artifact": ("[", "]"),
    #: A raw input: no artifact record names it as an output, so nothing in the
    #: project accounts for where it came from.
    "source": ("(", ")"),
    #: An environment lock.
    "environment": ("[(", ")]"),
    #: A script the command named. Project source, not data to account for.
    "code": (">", "]"),
}


@dataclass
class Node:
    id: str
    label: str
    kind: str
    state: State | None = None


@dataclass
class Edge:
    source: str
    target: str


@dataclass
class Graph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.edges


def build(root: Path, reports: list[ArtifactStatus] | None = None) -> Graph:
    """The whole project's derivation chain, from the record."""
    store = Store.load(root)
    reports = reports if reports is not None else check_all(root)
    by_path = {r.path: r for r in reports}

    graph = Graph()
    seen: dict[str, Node] = {}

    def node(path: str, kind: str) -> Node:
        if path not in seen:
            state = by_path[path].state if path in by_path else None
            seen[path] = Node(
                id=path, label=Path(path).name, kind=kind, state=state
            )
            graph.nodes.append(seen[path])
        elif kind == "artifact" and seen[path].kind not in (
            "artifact",
            "code",
            "environment",
        ):
            # A file can be an input before it is seen as an output, and the
            # artifact reading is usually the stronger one. Code and locks are
            # the exception: declaring a script records it as an artifact, but
            # what it *is* has not changed, and the shape should still say
            # "script" rather than flattening it into the boxes around it.
            seen[path].kind = "artifact"
        return seen[path]

    for path, artifact in sorted(store.artifacts.items()):
        node(path, "artifact")
        for item in artifact.inputs:
            if item.kind in ("environment", "code"):
                kind = item.kind
            else:
                kind = "artifact" if item.path in store.artifacts else "source"
            node(item.path, kind)
            graph.edges.append(Edge(item.path, path))

    graph.edges.sort(key=lambda e: (e.source, e.target))
    return graph


def _identifier(path: str, seen: dict[str, str]) -> str:
    """A safe, stable, unique Mermaid identifier."""
    if path in seen:
        return seen[path]
    slug = re.sub(r"[^A-Za-z0-9]", "_", path).strip("_") or "n"
    if slug[0].isdigit():
        slug = f"n{slug}"
    candidate, suffix = slug, 2
    used = set(seen.values())
    while candidate in used:
        candidate, suffix = f"{slug}_{suffix}", suffix + 1
    seen[path] = candidate
    return candidate


def _quote(label: str) -> str:
    """Mermaid label text. Quotes and brackets both break the parser."""
    cleaned = label.replace('"', "'").replace("[", "(").replace("]", ")")
    return f'"{cleaned}"'


def to_mermaid(graph: Graph, *, direction: str = "LR") -> str:
    """Render a graph as a Mermaid flowchart."""
    ids: dict[str, str] = {}
    lines = [f"graph {direction}"]

    for node in graph.nodes:
        open_, close = SHAPES.get(node.kind, SHAPES["source"])
        label = node.label
        if node.state is not None and node.state is not State.OK:
            # Marked in the label rather than by color alone: a reader
            # skimming the diagram should see the problem without consulting a
            # legend, and color is the first thing lost in print.
            label = f"{label} ⚠"
        lines.append(
            f"  {_identifier(node.id, ids)}{open_}{_quote(label)}{close}"
        )

    for edge in graph.edges:
        lines.append(
            f"  {_identifier(edge.source, ids)} --> {_identifier(edge.target, ids)}"
        )

    stale = [
        n
        for n in graph.nodes
        if n.state is not None and n.state is not State.OK
    ]
    if stale:
        lines.append("  classDef stale stroke-width:2px")
        joined = ",".join(_identifier(n.id, ids) for n in stale)
        lines.append(f"  class {joined} stale")

    return "\n".join(lines)


def project_mermaid(root: Path, *, direction: str = "LR") -> str:
    """The project's provenance DAG, ready for a MyST block."""
    return to_mermaid(build(root), direction=direction)
