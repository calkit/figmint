"""The record, drawn.

`provenance.toml` is already a DAG, so the graph needs no second source of
truth — which is the property worth protecting. A diagram that could disagree
with the freshness reported beside it would be worse than no diagram.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fromwhere.graph import build, project_mermaid, to_mermaid
from fromwhere.status import State
from fromwhere.store import Artifact, Input, Store, hash_file


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    for name in (
        "data.csv",
        "uv.lock",
        "plot.py",
        "plot.png",
        "turbine.png",
        "c.drawio",
        "c.svg",
    ):
        (tmp_path / name).write_text(name)

    store = Store.load(tmp_path)
    store.record(
        Artifact(
            path="plot.png",
            hash=hash_file(tmp_path / "plot.png"),
            inputs=[
                Input("data.csv", hash_file(tmp_path / "data.csv")),
                Input(
                    "uv.lock",
                    hash_file(tmp_path / "uv.lock"),
                    kind="environment",
                ),
                Input("plot.py", hash_file(tmp_path / "plot.py"), kind="code"),
            ],
        )
    )
    store.record(
        Artifact(
            path="c.drawio",
            hash=hash_file(tmp_path / "c.drawio"),
            inputs=[
                Input("plot.png", hash_file(tmp_path / "plot.png")),
                # Imported: nothing in the project produces it.
                Input("turbine.png", hash_file(tmp_path / "turbine.png")),
            ],
        )
    )
    store.record(
        Artifact(
            path="c.svg",
            hash=hash_file(tmp_path / "c.svg"),
            inputs=[Input("c.drawio", hash_file(tmp_path / "c.drawio"))],
        )
    )
    store.save()
    return tmp_path


class TestBuilding:
    def test_the_whole_chain_is_present(self, project: Path):
        graph = build(project)
        assert {n.id for n in graph.nodes} == {
            "data.csv",
            "uv.lock",
            "plot.py",
            "plot.png",
            "turbine.png",
            "c.drawio",
            "c.svg",
        }

    def test_edges_run_from_input_to_output(self, project: Path):
        graph = build(project)
        assert ("plot.png", "c.drawio") in {
            (e.source, e.target) for e in graph.edges
        }
        assert ("c.drawio", "c.svg") in {
            (e.source, e.target) for e in graph.edges
        }

    def test_data_and_environment_run_through_the_script(self, project: Path):
        """The script is what did the work, not a third ingredient beside the
        data.

        Drawn flat, a figure looks like the sum of a CSV, a lock file and some
        code — which is not how anyone thinks about it. The data was read by
        the script, and the script ran under the environment, so the picture
        is a chain rather than a fan-in.
        """
        edges = {(e.source, e.target) for e in build(project).edges}
        assert ("data.csv", "plot.py") in edges
        assert ("uv.lock", "plot.py") in edges
        assert ("plot.py", "plot.png") in edges
        # And nothing takes the short way round it.
        assert ("data.csv", "plot.png") not in edges
        assert ("uv.lock", "plot.png") not in edges

    def test_two_scripts_stay_a_fan_in(self, tmp_path: Path):
        """With one script the routing is a reading of the record; with two
        there is no saying which read the data, and a guess in a provenance
        diagram is worse than a fan-in."""
        (tmp_path / ".git").mkdir()
        for name in ("data.csv", "a.py", "b.py", "out.png"):
            (tmp_path / name).write_text(name)
        store = Store.load(tmp_path)
        store.record(
            Artifact(
                path="out.png",
                hash=hash_file(tmp_path / "out.png"),
                inputs=[
                    Input("data.csv", hash_file(tmp_path / "data.csv")),
                    Input("a.py", hash_file(tmp_path / "a.py"), kind="code"),
                    Input("b.py", hash_file(tmp_path / "b.py"), kind="code"),
                ],
            )
        )
        store.save()
        edges = {(e.source, e.target) for e in build(tmp_path).edges}
        assert ("data.csv", "out.png") in edges
        assert ("a.py", "out.png") in edges
        assert ("b.py", "out.png") in edges

    def test_a_document_with_no_script_keeps_its_lock_edge(
        self, tmp_path: Path
    ):
        """Nothing to route through: a page built by `quarto render` has no
        script, so the lock still points straight at the output."""
        (tmp_path / ".git").mkdir()
        for name in ("index.qmd", "uv.lock", "index.html"):
            (tmp_path / name).write_text(name)
        store = Store.load(tmp_path)
        store.record(
            Artifact(
                path="index.html",
                hash=hash_file(tmp_path / "index.html"),
                inputs=[
                    Input("index.qmd", hash_file(tmp_path / "index.qmd")),
                    Input(
                        "uv.lock",
                        hash_file(tmp_path / "uv.lock"),
                        kind="environment",
                    ),
                ],
            )
        )
        store.save()
        edges = {(e.source, e.target) for e in build(tmp_path).edges}
        assert ("uv.lock", "index.html") in edges
        assert ("index.qmd", "index.html") in edges

    def test_a_recorded_file_is_an_artifact_even_when_used_as_an_input(
        self, project: Path
    ):
        """`plot.png` is seen as an input before it is seen as an output.

        The artifact reading is the stronger one: it means fromwhere watched
        the file being made rather than merely being used.
        """
        kinds = {n.id: n.kind for n in build(project).nodes}
        assert kinds["plot.png"] == "artifact"

    def test_an_imported_file_is_a_source(self, project: Path):
        """Nothing in the project produced it, which is exactly the fact worth
        showing about an image somebody was handed."""
        kinds = {n.id: n.kind for n in build(project).nodes}
        assert kinds["turbine.png"] == "source"
        assert kinds["data.csv"] == "source"

    def test_a_lock_is_distinguished(self, project: Path):
        kinds = {n.id: n.kind for n in build(project).nodes}
        assert kinds["uv.lock"] == "environment"

    def test_state_is_carried_onto_the_nodes(self, project: Path):
        (project / "data.csv").write_text("changed")
        states = {n.id: n.state for n in build(project).nodes}
        assert states["plot.png"] is State.STALE

    def test_output_is_deterministic(self, project: Path):
        first, second = build(project), build(project)
        assert [n.id for n in first.nodes] == [n.id for n in second.nodes]
        assert [(e.source, e.target) for e in first.edges] == [
            (e.source, e.target) for e in second.edges
        ]

    def test_an_empty_project_is_empty_not_an_error(self, tmp_path: Path):
        assert build(tmp_path).empty


class TestMermaid:
    def test_it_renders_a_flowchart(self, project: Path):
        out = project_mermaid(project)
        assert out.startswith("graph LR")
        assert '"plot.png"' in out
        assert "-->" in out

    def test_direction_is_configurable(self, project: Path):
        assert to_mermaid(build(project), direction="TD").startswith(
            "graph TD"
        )

    def test_shapes_distinguish_the_kinds(self, project: Path):
        out = project_mermaid(project)
        assert '["plot.png"]' in out  # artifact
        assert '("turbine.png")' in out  # source
        assert '[("uv.lock")]' in out  # environment

    def test_a_stale_node_is_marked_in_the_label(self, project: Path):
        """Not by color alone: color is the first thing lost in print, and a
        reader skimming the diagram should see the problem without a legend."""
        (project / "data.csv").write_text("changed")
        out = project_mermaid(project)
        assert "plot.png ⚠" in out
        assert "classDef stale" in out

    def test_identifiers_are_safe(self, project: Path):
        """Mermaid ids cannot contain the punctuation paths are full of."""
        out = project_mermaid(project)
        for line in out.splitlines()[1:]:
            if "-->" in line or line.strip().startswith(
                ("classDef", "class ")
            ):
                continue
            # Every shape opener, `>` included: a script node is `id>"label"]`
            # and splitting only on brackets would read the label as part of
            # the identifier and pass whatever it found there.
            identifier = re.split(r"[\[(>]", line.strip())[0]
            assert "." not in identifier and "/" not in identifier

    def test_distinct_paths_do_not_collide(self, tmp_path: Path):
        """Sanitising ids can map two different paths onto one name."""
        (tmp_path / ".git").mkdir()
        store = Store.load(tmp_path)
        store.record(
            Artifact(
                path="a-b.png",
                hash="sha256:1",
                inputs=[Input("x.csv", "sha256:3")],
            )
        )
        store.record(
            Artifact(
                path="a_b.png",
                hash="sha256:2",
                inputs=[Input("x.csv", "sha256:3")],
            )
        )
        store.save()
        out = project_mermaid(tmp_path)
        identifiers = {
            line.strip().split("[")[0].split("(")[0]
            for line in out.splitlines()[1:]
            if "-->" not in line
            and not line.strip().startswith(("classDef", "class "))
        }
        assert len(identifiers) == 3

    def test_label_punctuation_cannot_break_the_parser(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        store = Store.load(tmp_path)
        store.record(
            Artifact(
                path='we"ird [name].png',
                hash="sha256:1",
                inputs=[Input("x.csv", "sha256:2")],
            )
        )
        store.save()
        out = project_mermaid(tmp_path)
        assert '"' in out
        assert "[name]" not in out
