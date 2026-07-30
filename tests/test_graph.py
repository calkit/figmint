"""The provenance-graph projection and its Mermaid rendering.

These tests use hand-built graphs rather than the Stencila SDK, deliberately.
The SDK is an optional dependency behind a multi-minute Rust build, and what is
worth pinning here is the *policy* ported from Stencila's TypeScript projection —
which edge kinds answer which question, what counts as scaffolding, how nodes are
classified. That policy is figmint's to keep faithful, and it can drift without
the SDK being involved at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from figmint.graph import (
    EDGE_PRESETS_BY_KIND,
    STRUCTURE_EDGE_KIND,
    GraphView,
    ViewEdge,
    ViewNode,
    edge_label,
    node_kind,
    node_label,
    project,
    to_mermaid,
)


@dataclass
class FakeNode:
    id: str
    node: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeEdge:
    source: str
    target: str
    kind: str


@dataclass
class FakeGraph:
    nodes: list[FakeNode]
    edges: list[FakeEdge]
    subject: str = "asset:figures/plot.svg"


@pytest.fixture
def graph() -> FakeGraph:
    """The shape a real figure produces: script generates asset, imports a package."""
    return FakeGraph(
        nodes=[
            FakeNode("asset:figures/plot.svg", {"type": "ImageObject", "name": "plot.svg"}),
            FakeNode("code:scripts/plot.py", {"type": "SoftwareSourceCode", "name": "plot.py"}),
            FakeNode("package:pypi/matplotlib", {"type": "SoftwareSourceCode", "name": "matplotlib"}),
            FakeNode("symbol:scripts/plot.py:python:out", {"type": "Variable", "name": "out"}),
            FakeNode("dir:figures", {"type": "Directory", "name": "figures", "path": "figures"}),
            FakeNode("dir:scripts", {"type": "Directory", "name": "scripts", "path": "scripts"}),
            FakeNode("dir:.", {"type": "Directory", "name": "project", "path": "."}),
        ],
        edges=[
            FakeEdge("code:scripts/plot.py", "asset:figures/plot.svg", "Generated"),
            FakeEdge("package:pypi/matplotlib", "code:scripts/plot.py", "ImportedBy"),
            FakeEdge("symbol:scripts/plot.py:python:out", "code:scripts/plot.py", "PartOf"),
            FakeEdge("symbol:scripts/plot.py:python:out", "asset:figures/plot.svg", "DerivedInto"),
            FakeEdge("asset:figures/plot.svg", "dir:figures", STRUCTURE_EDGE_KIND),
            FakeEdge("code:scripts/plot.py", "dir:scripts", STRUCTURE_EDGE_KIND),
            FakeEdge("dir:figures", "dir:.", STRUCTURE_EDGE_KIND),
            FakeEdge("dir:scripts", "dir:.", STRUCTURE_EDGE_KIND),
        ],
    )


class TestVocabulary:
    """Ported verbatim from Stencila, so the tests guard against drift."""

    def test_the_preset_table_matches_stencila(self):
        # Spot-checks of the mapping that decides what each view answers.
        assert EDGE_PRESETS_BY_KIND["Generated"] == ("data-flow",)
        assert EDGE_PRESETS_BY_KIND["ImportedBy"] == ("software-dependencies",)
        assert EDGE_PRESETS_BY_KIND["UsedBy"] == ("data-flow", "software-dependencies")
        # Containment belongs to no preset; it is scaffolding, added separately.
        assert EDGE_PRESETS_BY_KIND[STRUCTURE_EDGE_KIND] == ()

    @pytest.mark.parametrize(
        ("node_id", "expected"),
        [
            ("dir:figures", "workspace"),
            ("code:scripts/plot.py", "code"),
            ("package:pypi/matplotlib", "package"),
            ("symbol:x:python:out", "symbol"),
            ("reference:doi", "reference"),
        ],
    )
    def test_the_id_namespace_decides_the_kind(self, node_id: str, expected: str):
        """Namespace first: it encodes what the graph builder meant."""
        assert node_kind(FakeNode(node_id)) == expected

    def test_the_schema_type_is_the_fallback(self):
        assert node_kind(FakeNode("x", {"type": "ImageObject"})) == "content"
        assert node_kind(FakeNode("x", {"type": "Variable"})) == "symbol"
        assert node_kind(FakeNode("x", {"type": "Nonsense"})) == "other"
        assert node_kind(None) == "other"

    def test_labels_prefer_a_human_field(self):
        assert node_label(FakeNode("code:a/b.py", {"name": "b.py"})) == "b.py"
        assert node_label(FakeNode("code:a/b.py", {"path": "a/b.py"})) == "b.py"
        # No usable field: the id, with its namespace and path stripped.
        assert node_label(FakeNode("code:a/b.py")) == "b.py"

    def test_long_labels_are_truncated(self):
        label = node_label(FakeNode("x", {"name": "n" * 80}))
        assert len(label) == 42 and label.endswith("...")

    def test_edge_labels_split_only_at_word_boundaries(self):
        assert edge_label("DerivedInto") == "Derived Into"
        assert edge_label("PartOf") == "Part Of"


class TestProjection:
    def test_auto_picks_data_flow_when_a_generation_edge_exists(self, graph):
        assert project(graph).preset == "data-flow"

    def test_auto_falls_through_to_dependencies(self, graph):
        graph.edges = [e for e in graph.edges if e.kind not in ("Generated", "DerivedInto")]
        assert project(graph).preset == "software-dependencies"

    def test_auto_falls_back_to_full(self, graph):
        graph.edges = [e for e in graph.edges if e.kind == STRUCTURE_EDGE_KIND]
        assert project(graph).preset == "full"

    def test_data_flow_hides_package_imports(self, graph):
        view = project(graph, preset="data-flow")
        ids = {n.id for n in view.nodes}
        assert "package:pypi/matplotlib" not in ids
        assert "code:scripts/plot.py" in ids

    def test_dependencies_hides_the_generated_asset(self, graph):
        view = project(graph, preset="software-dependencies")
        ids = {n.id for n in view.nodes}
        assert "package:pypi/matplotlib" in ids
        assert "asset:figures/plot.svg" not in ids

    def test_symbols_are_dropped_at_medium_detail(self, graph):
        """Real edges, too fine-grained for a reader looking at a figure."""
        view = project(graph, preset="data-flow", detail="medium")
        assert not any(n.kind == "symbol" for n in view.nodes)

    def test_high_detail_keeps_them(self, graph):
        view = project(graph, preset="data-flow", detail="high")
        assert any(n.kind == "symbol" for n in view.nodes)

    def test_structure_edges_are_never_primary(self, graph):
        view = project(graph, preset="data-flow")
        assert not any(e.kind == STRUCTURE_EDGE_KIND for e in view.edges)

    def test_structure_comes_back_as_ancestry_when_asked(self, graph):
        """Containment as context — the ancestors of what survived, not the tree."""
        view = project(graph, preset="data-flow", include_structure=True)
        ids = {n.id for n in view.nodes}
        assert {"dir:figures", "dir:scripts", "dir:."} <= ids
        # The symbol was filtered out, so its container must not be dragged in.
        assert "symbol:scripts/plot.py:python:out" not in ids

    def test_full_includes_every_node(self, graph):
        view = project(graph, preset="full")
        assert len(view.nodes) == len(graph.nodes)

    def test_edges_to_missing_nodes_are_dropped(self, graph):
        graph.edges.append(FakeEdge("code:scripts/plot.py", "code:ghost.py", "Generated"))
        view = project(graph, preset="data-flow")
        assert not any(e.target == "code:ghost.py" for e in view.edges)

    def test_output_is_deterministic(self, graph):
        first = project(graph, preset="full", include_structure=True)
        second = project(graph, preset="full", include_structure=True)
        assert [n.id for n in first.nodes] == [n.id for n in second.nodes]
        assert [(e.source, e.target) for e in first.edges] == [
            (e.source, e.target) for e in second.edges
        ]

    def test_a_structure_cycle_does_not_hang(self, graph):
        """Malformed input must not spin the ancestry walk forever."""
        graph.edges.append(FakeEdge("dir:.", "dir:figures", STRUCTURE_EDGE_KIND))
        view = project(graph, preset="data-flow", include_structure=True)
        assert view.nodes


class TestMermaid:
    def test_renders_nodes_and_labelled_edges(self, graph):
        out = to_mermaid(project(graph, preset="data-flow"))
        assert out.startswith("graph LR")
        assert '"plot.py"' in out
        assert '-->|"Generated"|' in out

    def test_structure_edges_are_dotted(self, graph):
        out = to_mermaid(project(graph, preset="data-flow", include_structure=True))
        assert "-.->" in out

    def test_identifiers_are_safe(self, graph):
        """Mermaid ids cannot contain the punctuation graph ids are full of."""
        out = to_mermaid(project(graph, preset="full"))
        for line in out.splitlines()[1:]:
            identifier = line.strip().split("[")[0].split("(")[0].split(" ")[0]
            assert ":" not in identifier and "/" not in identifier

    def test_distinct_ids_do_not_collide(self):
        """Sanitising ids can map two different nodes onto one name."""
        g = FakeGraph(
            nodes=[FakeNode("a:b", {"name": "x"}), FakeNode("a/b", {"name": "y"})],
            edges=[FakeEdge("a:b", "a/b", "Generated")],
        )
        out = to_mermaid(project(g, preset="full"))
        identifiers = {
            line.strip().split("[")[0].split("(")[0]
            for line in out.splitlines()[1:]
            if "-->" not in line and "-.->" not in line
        }
        assert len(identifiers) == 2

    def test_label_punctuation_cannot_break_the_parser(self):
        g = FakeGraph(
            nodes=[FakeNode("code:a", {"name": 'we"ird [label]'}), FakeNode("asset:b", {"name": "b"})],
            edges=[FakeEdge("code:a", "asset:b", "Generated")],
        )
        out = to_mermaid(project(g, preset="full"))
        label = [ln for ln in out.splitlines() if "ird" in ln][0]
        assert '"' == label.strip().split("[")[1][0]
        assert "[label]" not in label

    def test_direction_is_configurable(self, graph):
        assert to_mermaid(project(graph), direction="TD").startswith("graph TD")

    def test_an_empty_view_is_detectable(self):
        assert GraphView(preset="data-flow", detail="medium").empty
        assert not GraphView(
            preset="data-flow",
            detail="medium",
            nodes=[ViewNode("a", "a", "code")],
            edges=[ViewEdge("a", "b", "Generated")],
        ).empty


class TestDirectiveOption:
    def test_graph_option_parsing(self):
        from figmint.myst import graph_option

        assert graph_option(None) is None
        assert graph_option("false") is None
        assert graph_option("true") == "auto"
        assert graph_option(True) == "auto"
        assert graph_option("data-flow") == "data-flow"
        # An unrecognised name falls back rather than failing a build.
        assert graph_option("nonsense") == "auto"

    def test_a_missing_sdk_is_reported_not_raised(self, tmp_path, monkeypatch):
        """A document build must not die because an optional dependency is absent."""
        import figmint.graph as graph_mod
        from figmint.myst import graph_block
        from figmint.provenance import DEFAULT_POLICY
        from figmint.report import FigureReport

        def unavailable():
            raise graph_mod.GraphUnavailable("the Stencila SDK is not installed")

        monkeypatch.setattr(graph_mod, "_stencila_graph", unavailable)
        report = FigureReport(document=tmp_path / "f.drawio", policy=DEFAULT_POLICY)
        node = graph_block(report, tmp_path / "f.drawio", "auto", "medium")
        assert node["type"] == "paragraph"
        assert "unavailable" in str(node).lower()
