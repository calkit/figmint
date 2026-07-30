"""Rendering a figure's provenance graph as Mermaid.

Stencila builds the graph — what produced an asset, from what, through which
packages and symbols. Getting that into a document needs two things Stencila's
Python SDK does not provide: a *projection* that reduces the interchange graph to
something a reader can look at, and a renderer.

Stencila has both, in TypeScript: `web/src/graphs/project.ts` and
`cytoscape.ts`, behind a `<stencila-graph-view>` web component. Neither is
reachable from Python — `rust/graph/` renders only Graphviz DOT, and the sole
binding is `graph.prepare`. Vendoring the component into a MyST site means
shipping ~680 KB of JavaScript and pinning it to a Stencila version.

So the projection policy is ported here and rendered to Mermaid, which MyST draws
natively with no JavaScript of ours at all. What is ported is the *policy* —
which edge kinds answer which question, that `PartOf` is scaffolding rather than
subject, how a node is classified and labelled. That is the part with judgement
in it; the drawing is incidental. See `docs/stencila-integration.md`.

The port is deliberately partial. `data-flow` and `software-dependencies` are
what a figure's provenance actually contains; `citations` and `reactivity` exist
in the preset table so `auto` resolves the same way, but citation collapsing and
reactive-cell analysis are document and kernel concepts figmint never sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Vocabulary — ported from web/src/graphs/vocabulary.ts
# --------------------------------------------------------------------------

#: Containment. Treated as context rather than subject, so it is filtered out of
#: primary edges and added back only as ancestry around what survived.
STRUCTURE_EDGE_KIND = "PartOf"

Preset = str  # auto | full | data-flow | software-dependencies | citations | reactivity
Detail = str  # low | medium | high

#: Which question each relationship answers. Verbatim from Stencila, because
#: this table *is* the policy — inventing our own would mean a figure's graph
#: disagreeing with the same graph shown in Stencila's own viewer.
EDGE_PRESETS_BY_KIND: dict[str, tuple[str, ...]] = {
    "UsedBy": ("data-flow", "software-dependencies"),
    "ReadBy": ("data-flow",),
    "Generated": ("data-flow",),
    "WrittenTo": ("data-flow",),
    "DerivedInto": ("data-flow",),
    "ConvertedInto": ("data-flow",),
    "CalledBy": ("data-flow", "software-dependencies"),
    "ImportedBy": ("software-dependencies",),
    "PartOf": (),
    "IncludedBy": ("data-flow",),
    "LinkedBy": ("citations",),
    "CitedBy": ("citations",),
    "Declares": ("software-dependencies",),
    "Configures": ("data-flow", "software-dependencies"),
    "RequiredBy": ("software-dependencies",),
    "Pins": ("software-dependencies",),
}

#: The order `auto` tries, most common authoring question first.
AUTO_PRESETS = ("data-flow", "software-dependencies", "citations", "reactivity")

#: Graph id namespace -> display category.
_NAMESPACE_KINDS = {
    "dir": "workspace",
    "environment": "environment",
    "file": "resource",
    "symlink": "resource",
    "resource": "resource",
    "code-file": "resource",
    "credential": "resource",
    "ingredient": "resource",
    "agent": "resource",
    "code": "code",
    "symbol": "symbol",
    "function": "function",
    "workflow-rule": "function",
    "package": "package",
    "column": "datatable",
    "reference": "reference",
    "output": "output",
}

_TYPE_KINDS = {
    "CodeBlock": "code",
    "CodeChunk": "code",
    "CodeExpression": "code",
    "CodeInline": "code",
    "SoftwareSourceCode": "code",
    "Parameter": "symbol",
    "Variable": "symbol",
    "Function": "function",
    "Datatable": "datatable",
    "DatatableColumn": "datatable",
    "Directory": "workspace",
    "File": "resource",
    "SymbolicLink": "resource",
    "Article": "document",
    "Collection": "document",
    "Prompt": "document",
    "Reference": "reference",
}

_CONTENT_TYPES = {
    "AudioObject",
    "CitationGroup",
    "Figure",
    "Heading",
    "ImageObject",
    "IncludeBlock",
    "Link",
    "MediaObject",
    "Table",
    "VideoObject",
}


def edge_label(kind: str) -> str:
    """`DerivedInto` -> `Derived Into`, splitting only at word boundaries."""
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", kind)


def _namespace(node_id: str) -> str:
    head, sep, _ = node_id.partition(":")
    return head if sep else node_id


def _payload(node: Any) -> Any:
    """The schema node inside a `GraphNode`, whichever shape it arrived in."""
    return getattr(node, "node", None) if not isinstance(node, dict) else node.get("node")


def _field(payload: Any, name: str) -> Any:
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload.get(name)
    return getattr(payload, name, None)


def node_kind(node: Any) -> str:
    """Classify a node for display.

    Prefers the graph id namespace, which encodes graph-construction intent,
    and falls back to the schema type for document-derived nodes.
    """
    if node is None:
        return "other"

    node_id = _field(node, "id") or ""
    kind = _NAMESPACE_KINDS.get(_namespace(node_id))
    if kind:
        return kind

    payload = _payload(node)
    node_type = _field(payload, "type")

    if node_type == "Citation" or "#citation" in node_id:
        return "citation"
    if node_type == "CreativeWork":
        return "resource" if _namespace(node_id) == "resource" else "reference"
    if node_type in _TYPE_KINDS:
        return _TYPE_KINDS[node_type]
    if node_type in _CONTENT_TYPES:
        return "content"
    return "other"


def _string_value(value: Any) -> str | None:
    """Pull text out of the shapes a schema label can take."""
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, (list, tuple)):
        parts = [p for p in (_string_value(v) for v in value) if p]
        return " ".join(parts).strip() or None
    if value is not None and not isinstance(value, (int, float, bool)):
        for key in ("value", "text", "content"):
            found = _string_value(_field(value, key))
            if found:
                return found
    return None


def _compact(label: str) -> str:
    value = re.sub(r"^([a-z]+):", "", label)
    value = re.sub(r"^.*[/#]", "", value)
    return f"{value[:39]}..." if len(value) > 42 else value


def node_label(node: Any) -> str:
    payload = _payload(node)
    for key in ("name", "title", "path", "url", "target", "id"):
        label = _string_value(_field(payload, key))
        if label:
            return _compact(label)
    return _compact(_field(node, "id") or "?")


# --------------------------------------------------------------------------
# Projection — ported from web/src/graphs/project.ts
# --------------------------------------------------------------------------


@dataclass
class ViewNode:
    id: str
    label: str
    kind: str


@dataclass
class ViewEdge:
    source: str
    target: str
    kind: str


@dataclass
class GraphView:
    preset: str
    detail: str
    nodes: list[ViewNode] = field(default_factory=list)
    edges: list[ViewEdge] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.edges


def _is_local_code_internal(node_id: str, kind: str) -> bool:
    """Symbols and local functions: real edges, too fine-grained to show."""
    return kind == "symbol" or (
        kind == "function" and _namespace(node_id) != "workflow-rule"
    )


def _include_for_detail(
    edge: Any, preset: str, detail: str, nodes_by_id: dict[str, Any]
) -> bool:
    if preset in ("full", "reactivity", "citations") or detail == "high":
        return True

    source_kind = node_kind(nodes_by_id.get(edge.source))
    target_kind = node_kind(nodes_by_id.get(edge.target))
    source_internal = _is_local_code_internal(edge.source, source_kind)
    target_internal = _is_local_code_internal(edge.target, target_kind)
    source_data = source_kind == "datatable"
    target_data = target_kind == "datatable"

    if preset == "data-flow":
        if detail == "low":
            return not (source_internal or target_internal or source_data or target_data)
        return (
            not source_internal
            and not target_internal
            and (not (source_data or target_data) or edge.kind == "DerivedInto")
        )
    if preset == "software-dependencies":
        return not source_internal and not target_internal
    return True


def _is_reactive(edge: Any, nodes_by_id: dict[str, Any]) -> bool:
    source_kind = node_kind(nodes_by_id.get(edge.source))
    target_kind = node_kind(nodes_by_id.get(edge.target))
    if edge.kind == "Generated":
        return source_kind == "code" and target_kind in ("function", "symbol")
    if edge.kind == "UsedBy":
        return source_kind in ("function", "symbol") and target_kind == "code"
    return False


def _include_primary(
    edge: Any, preset: str, detail: str, nodes_by_id: dict[str, Any]
) -> bool:
    if edge.kind == STRUCTURE_EDGE_KIND:
        return False
    if preset == "reactivity":
        return _is_reactive(edge, nodes_by_id)
    if preset != "full" and preset not in EDGE_PRESETS_BY_KIND.get(edge.kind, ()):
        return False
    return _include_for_detail(edge, preset, detail, nodes_by_id)


def _resolve_preset(graph: Any, preset: str, detail: str, nodes_by_id) -> str:
    if preset != "auto":
        return preset
    for candidate in AUTO_PRESETS:
        if any(
            _include_primary(edge, candidate, detail, nodes_by_id)
            for edge in graph.edges
        ):
            return candidate
    return "full"


def _parent_map(graph: Any) -> dict[str, str]:
    return {
        edge.source: edge.target
        for edge in graph.edges
        if edge.kind == STRUCTURE_EDGE_KIND
    }


def project(
    graph: Any,
    *,
    preset: str = "auto",
    detail: str = "medium",
    include_structure: bool | None = None,
) -> GraphView:
    """Reduce an interchange graph to something worth showing a reader.

    Structure edges come back only as ancestry of nodes that survived filtering,
    which is what keeps "where in the project did this happen" without dragging
    in the whole directory tree.
    """
    nodes_by_id = {n.id: n for n in graph.nodes}
    resolved = _resolve_preset(graph, preset, detail, nodes_by_id)
    if include_structure is None:
        include_structure = resolved == "full"

    node_ids: set[str] = set()
    edges: dict[tuple[str, str, str], ViewEdge] = {}

    for edge in graph.edges:
        if not _include_primary(edge, resolved, detail, nodes_by_id):
            continue
        if edge.source not in nodes_by_id or edge.target not in nodes_by_id:
            continue
        node_ids.add(edge.source)
        node_ids.add(edge.target)
        edges[(edge.source, edge.target, edge.kind)] = ViewEdge(
            edge.source, edge.target, edge.kind
        )

    if resolved == "full":
        node_ids.update(nodes_by_id)

    if include_structure:
        parents = _parent_map(graph)
        for node_id in list(node_ids):
            current = node_id
            # Walk up, guarding against a cycle in malformed input.
            for _ in range(len(nodes_by_id)):
                parent = parents.get(current)
                if parent is None or parent not in nodes_by_id:
                    break
                key = (current, parent, STRUCTURE_EDGE_KIND)
                if key in edges:
                    break
                node_ids.add(parent)
                edges[key] = ViewEdge(current, parent, STRUCTURE_EDGE_KIND)
                current = parent

    nodes = [
        ViewNode(node_id, node_label(nodes_by_id[node_id]), node_kind(nodes_by_id[node_id]))
        for node_id in sorted(node_ids)
    ]
    return GraphView(
        preset=resolved,
        detail=detail,
        nodes=nodes,
        edges=sorted(edges.values(), key=lambda e: (e.source, e.target, e.kind)),
    )


# --------------------------------------------------------------------------
# Mermaid
# --------------------------------------------------------------------------

#: Node shape per display kind. Mermaid's vocabulary is small, so this groups
#: rather than distinguishing everything: the reader needs to tell code from
#: data from a package, not to learn a legend.
_SHAPES: dict[str, tuple[str, str]] = {
    "code": ("[", "]"),
    "package": ("([", "])"),
    "workspace": ("[(", ")]"),
    "resource": ("(", ")"),
    "content": ("(", ")"),
    "output": ("(", ")"),
    "datatable": ("[(", ")]"),
    "symbol": (">", "]"),
    "function": ("{{", "}}"),
}
_DEFAULT_SHAPE = ("[", "]")


def _mermaid_id(node_id: str, seen: dict[str, str]) -> str:
    """A safe, stable, unique Mermaid identifier."""
    if node_id in seen:
        return seen[node_id]
    slug = re.sub(r"[^A-Za-z0-9]", "_", node_id).strip("_") or "n"
    if slug[0].isdigit():
        slug = f"n{slug}"
    candidate, suffix = slug, 2
    used = set(seen.values())
    while candidate in used:
        candidate, suffix = f"{slug}_{suffix}", suffix + 1
    seen[node_id] = candidate
    return candidate


def _quote(label: str) -> str:
    """Mermaid label text. Quotes and brackets both break the parser."""
    cleaned = label.replace('"', "'").replace("[", "(").replace("]", ")")
    return f'"{cleaned}"'


def to_mermaid(view: GraphView, *, direction: str = "LR") -> str:
    """Render a projected view as a Mermaid flowchart."""
    ids: dict[str, str] = {}
    lines = [f"graph {direction}"]

    for node in view.nodes:
        open_, close = _SHAPES.get(node.kind, _DEFAULT_SHAPE)
        lines.append(f"  {_mermaid_id(node.id, ids)}{open_}{_quote(node.label)}{close}")

    for edge in view.edges:
        source = _mermaid_id(edge.source, ids)
        target = _mermaid_id(edge.target, ids)
        if edge.kind == STRUCTURE_EDGE_KIND:
            # Dotted, because containment is context rather than derivation.
            lines.append(f"  {source} -.-> {target}")
        else:
            lines.append(f"  {source} -->|{_quote(edge_label(edge.kind))}| {target}")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Building a figure's graph
# --------------------------------------------------------------------------


#: The id every Stencila graph gives its own subject, regardless of the file.
SUBJECT_ID = "asset:signed"


@dataclass
class _Renamed:
    """A graph node under a merge-safe id, delegating everything else."""

    id: str
    _node: Any

    @property
    def node(self) -> Any:
        return _payload(self._node)


class GraphUnavailable(RuntimeError):
    """Raised when a provenance graph cannot be produced."""


def _stencila_graph():
    try:
        from stencila import graph as stencila_graph
    except ImportError as exc:  # pragma: no cover - depends on install
        raise GraphUnavailable(
            "the Stencila SDK is not installed; "
            "`uv sync --group stencila` to enable provenance graphs"
        ) from exc
    return stencila_graph


@dataclass
class _Merged:
    """Union of several per-component graphs, deduplicated by node id."""

    nodes: list[Any] = field(default_factory=list)
    edges: list[Any] = field(default_factory=list)


def figure_graph(report, root: Path, *, profile: str = "public") -> Any:
    """One graph for a whole figure, merged from its components.

    Stencila builds a graph per asset. A composite figure is several assets, and
    the question a reader has is about the figure, so the per-component graphs
    are merged rather than shown separately.

    `source` is supplied from the Calkit stage rather than left to inference: for
    a file subject the SDK does not infer one, and without it the graph is three
    nodes of directory containment. figmint already knows the answer.
    """
    from . import calkit as calkit_mod

    build = _stencila_graph()
    project_ = calkit_mod.project_for(root)

    merged = _Merged()
    seen_nodes: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()
    built = 0

    for component in report.components:
        if component.resolved is None or not component.resolved.is_file():
            continue
        stage = project_.stage_for(component.resolved) if project_ else None
        source = None
        if stage is not None and stage.entrypoint:
            candidate = root / stage.entrypoint
            if candidate.is_file():
                source = str(candidate)
        try:
            graph = build(
                str(component.resolved),
                source=source,
                workspace=str(root),
                profile=profile,
            )
        except Exception as exc:  # noqa: BLE001 - one bad component must not kill the build
            raise GraphUnavailable(f"{component.label}: {exc}") from exc

        built += 1

        # Every graph names its own subject `asset:signed`. Merging without
        # renaming would silently fuse two different components into one node —
        # a composite figure would come out claiming its panels were the same
        # file. `graph.subject` carries the distinguishing id, so use that.
        rename = {SUBJECT_ID: graph.subject} if graph.subject else {}

        for node in graph.nodes:
            node_id = rename.get(node.id, node.id)
            if node_id not in seen_nodes:
                seen_nodes.add(node_id)
                merged.nodes.append(_Renamed(node_id, node))
        for edge in graph.edges:
            source = rename.get(edge.source, edge.source)
            target = rename.get(edge.target, edge.target)
            key = (source, target, edge.kind)
            if key not in seen_edges:
                seen_edges.add(key)
                merged.edges.append(ViewEdge(source, target, edge.kind))

    if not built:
        raise GraphUnavailable("no components with files on disk")
    return merged


def figure_mermaid(
    report,
    root: Path,
    *,
    preset: str = "auto",
    detail: str = "medium",
    direction: str = "LR",
) -> str:
    """A figure's provenance, projected and rendered, ready for a MyST block."""
    view = project(figure_graph(report, root), preset=preset, detail=detail)
    if view.empty:
        raise GraphUnavailable("no relationships to show at this preset")
    return to_mermaid(view, direction=direction)
