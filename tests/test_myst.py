"""The MyST plugin: the executable protocol, and what it renders.

The value is mostly in the protocol. mystmd talks to this plugin by spawning it
and exchanging JSON on stdin/stdout, so a broken spec or a stray `print` shows
up as an opaque "Non-zero exit code" during someone's document build, with no
stack trace. Exercising the real entry point catches that.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from figmint.myst import (
    SPEC,
    chain_items,
    document_directive,
    main,
    run_directive,
)
from figmint.status import State, check_path
from figmint.store import Artifact, Input, Store, hash_file


def payload(target: str, **options) -> dict:
    """The shape mystmd hands a directive, reduced to what the plugin reads."""
    return {
        "name": "figmint",
        "arg": target,
        "options": options,
        "body": "A caption.",
        "node": {
            "type": "mystDirective",
            "children": [
                {
                    "type": "mystDirectiveBody",
                    "children": [
                        {
                            "type": "paragraph",
                            "children": [
                                {"type": "text", "value": "A caption."}
                            ],
                        }
                    ],
                }
            ],
        },
    }


def find(node, node_type: str):
    if isinstance(node, list):
        for item in node:
            hit = find(item, node_type)
            if hit is not None:
                return hit
        return None
    if not isinstance(node, dict):
        return None
    if node.get("type") == node_type:
        return node
    return find(node.get("children") or [], node_type)


def all_text(node) -> str:
    if isinstance(node, list):
        return " ".join(all_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    # `inlineCode` carries its content in `value` with no children, so a
    # text-only walk would miss every path the panel names.
    value = (
        node.get("value") if node.get("type") in ("text", "inlineCode") else ""
    )
    return " ".join(
        [value or "", all_text(node.get("children") or [])]
    ).strip()


def _creds(machine_generated: bool):
    """A stand-in for a parsed C2PA manifest, with the fields the panel reads."""
    from types import SimpleNamespace

    return SimpleNamespace(
        machineGenerated=machine_generated,
        validationState="Valid",
        signedBy="a test",
        warnings=[],
        digitalSourceType=None,
        machineGeneratedBy=None,
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "data.csv").write_text("x,y\n1,2\n")
    (tmp_path / "uv.lock").write_text("lock")
    (tmp_path / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\nplot")

    store = Store.load(tmp_path)
    store.record(
        Artifact(
            path="plot.png",
            hash=hash_file(tmp_path / "plot.png"),
            command="uv run plot.py",
            inputs=[
                Input("data.csv", hash_file(tmp_path / "data.csv")),
                Input(
                    "uv.lock",
                    hash_file(tmp_path / "uv.lock"),
                    kind="environment",
                ),
            ],
        )
    )
    store.save()
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestProtocol:
    def test_no_arguments_prints_the_spec(self, capsys):
        assert main([]) == 0
        spec = json.loads(capsys.readouterr().out)
        assert [d["name"] for d in spec["directives"]] == [
            "figmint",
            "figmint-provenance",
        ]

    def test_the_spec_is_json_serialisable(self):
        # An enum or a Path would sneak past a unit test and fail only when
        # mystmd parses stdout.
        json.dumps(SPEC)

    def test_the_real_executable_answers(self, project: Path):
        """Spawned the way mystmd does it, not called in-process."""
        spec = subprocess.run(
            [sys.executable, "-m", "figmint.myst"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(spec.stdout)["name"] == "figmint"

        result = subprocess.run(
            [sys.executable, "-m", "figmint.myst", "--directive", "figmint"],
            input=json.dumps(payload("plot.png")),
            capture_output=True,
            text=True,
            cwd=project,
            check=True,
        )
        assert find(json.loads(result.stdout), "container")["kind"] == "figure"

    def test_an_unknown_request_fails_loudly(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
        assert main(["--role", "nope"]) == 1
        assert "unsupported request" in capsys.readouterr().err


class TestFigureNode:
    def test_it_emits_a_figure_container(self, project: Path):
        assert (
            find(run_directive(payload("plot.png")), "container")["kind"]
            == "figure"
        )

    def test_the_name_option_becomes_a_label(self, project: Path):
        container = find(
            run_directive(payload("plot.png", name="fig-x")), "container"
        )
        # Lowercased identifier plus original label is what MyST's own figure
        # directive produces, and cross-references depend on it.
        assert container["identifier"] == "fig-x"
        assert container["label"] == "fig-x"

    def test_the_caption_keeps_its_parsed_children(self, project: Path):
        """Reusing the parsed body is what keeps emphasis and citations working."""
        caption = find(run_directive(payload("plot.png")), "caption")
        assert find(caption, "paragraph") is not None

    def test_the_image_url_is_left_relative(self, project: Path):
        """MyST resolves and copies the asset itself; an absolute path breaks that."""
        assert (
            find(run_directive(payload("plot.png")), "image")["url"]
            == "plot.png"
        )

    def test_a_non_image_artifact_gets_no_image_node(self, project: Path):
        """A dataset has provenance worth showing and nothing to display.

        Emitting an `image` node for a CSV makes MyST warn about an unsupported
        extension and renders a broken picture where a filename belongs.
        """
        nodes = run_directive(payload("data.csv"))
        assert find(nodes, "image") is None
        assert "data.csv" in all_text(nodes[0])
        assert find(nodes, "admonition") is not None


class TestPanel:
    def test_a_current_artifact_is_collapsed(self, project: Path):
        panel = find(run_directive(payload("plot.png")), "admonition")
        assert panel["kind"] == "note"
        # Collapsed when nothing is wrong, so a long document does not become a
        # wall of metadata.
        assert panel["class"] == "dropdown"
        assert "Up to date" in all_text(panel)

    def test_the_inputs_are_listed(self, project: Path):
        panel = find(run_directive(payload("plot.png")), "admonition")
        text = all_text(panel)
        assert "data.csv" in text
        assert "uv.lock" in text
        assert "environment" in text

    def test_an_undeclared_input_is_called_out(self, project: Path):
        """The one thing in the panel that is wrong while every check passes."""
        panel = find(run_directive(payload("plot.png")), "admonition")
        assert "undeclared" in all_text(panel)

    def test_a_declared_input_shows_its_origin(self, project: Path):
        """The provenance of the components, not just their names.

        A composite figure is only as placeable as the pieces it is built from,
        so the panel says where each one came from.
        """
        from figmint.declare import declare
        from figmint.origins import Author, attested

        declare(project / "data.csv", attested(Author("A Researcher")))
        text = all_text(find(run_directive(payload("plot.png")), "admonition"))
        assert "created by A Researcher" in text
        assert "undeclared" not in text

    def test_a_machine_generated_input_names_both_tool_and_person(
        self, project: Path
    ):
        """A model cannot answer for a file, so the disclosure never appears
        without an accountable name attached to it."""
        from figmint.declare import declare
        from figmint.origins import Author, attested

        declare(
            project / "data.csv",
            attested(Author("A Researcher"), Author("Claude Opus 5", "ai")),
        )
        shown = all_text(
            find(run_directive(payload("plot.png")), "admonition")
        )
        assert "created by A Researcher and Claude Opus 5 (AI)" in shown

    def test_a_derived_input_is_named_as_such(self, project: Path):
        store = Store.load(project)
        store.record(
            Artifact(
                path="data.csv",
                hash=hash_file(project / "data.csv"),
                command="uv run collect.py",
            )
        )
        store.save()
        assert "derived by figmint" in all_text(
            find(run_directive(payload("plot.png")), "admonition")
        )

    def test_a_declared_artifact_shows_its_own_origin(self, project: Path):
        """A primary has no command and no inputs, so without this the panel has
        nothing to say about the very files a declaration exists for."""
        from figmint.declare import declare
        from figmint.origins import parse_location

        declare(
            project / "data.csv",
            parse_location("git", "github.com/u/p/data.csv@a1b2c3d"),
        )
        text = all_text(find(run_directive(payload("data.csv")), "admonition"))
        assert "git:github.com/u/p/data.csv@a1b2c3d" in text

    def test_an_attestation_is_not_dressed_up_as_proof(self, project: Path):
        from figmint.declare import declare
        from figmint.origins import Author, attested

        declare(project / "data.csv", attested(Author("A Researcher")))
        assert "nothing can verify it" in all_text(
            find(run_directive(payload("data.csv")), "admonition")
        )

    def test_the_whole_chain_is_listed_not_just_direct_inputs(
        self, project: Path
    ):
        """A composite's only direct input is a `.drawio`, which tells a reader
        nothing. The data, the script and the lock are all one level further
        back, and they are what the panel exists to show."""
        store = Store.load(project)
        (project / "composite.drawio").write_text("<mxfile/>")
        store.record(
            Artifact(
                path="composite.drawio",
                hash=hash_file(project / "composite.drawio"),
                kind="authored",
                inputs=[Input("plot.png", hash_file(project / "plot.png"))],
            )
        )
        store.save()

        panel = find(run_directive(payload("composite.drawio")), "admonition")
        listed = all_text(find(panel, "table"))
        # plot.png is direct; these are inherited from it.
        assert "data.csv" in listed
        assert "uv.lock" in listed
        assert (
            "assembled in draw.io" not in listed
        )  # that is the artifact itself

    def test_direct_inputs_come_first(self, project: Path):
        store = Store.load(project)
        (project / "composite.drawio").write_text("<mxfile/>")
        store.record(
            Artifact(
                path="composite.drawio",
                hash=hash_file(project / "composite.drawio"),
                kind="authored",
                inputs=[Input("plot.png", hash_file(project / "plot.png"))],
            )
        )
        store.save()
        rows = find(
            find(run_directive(payload("composite.drawio")), "admonition"),
            "table",
        )["children"]
        # Row 0 is the header.
        assert "plot.png" in all_text(rows[1])

    def test_a_hand_arranged_diagram_is_not_called_declared(
        self, project: Path
    ):
        """It has inputs and no command, but nobody declared it — and a diagram
        is arranged by hand afterwards, so naming a command would promise a
        reproduction that does not exist."""
        store = Store.load(project)
        (project / "composite.drawio").write_text("<mxfile/>")
        store.record(
            Artifact(
                path="composite.drawio",
                hash=hash_file(project / "composite.drawio"),
                kind="authored",
                inputs=[Input("plot.png", hash_file(project / "plot.png"))],
            )
        )
        (project / "out.svg").write_text("<svg/>")
        store.record(
            Artifact(
                path="out.svg",
                hash=hash_file(project / "out.svg"),
                command="figmint drawio export composite.drawio out.svg",
                inputs=[
                    Input(
                        "composite.drawio",
                        hash_file(project / "composite.drawio"),
                    )
                ],
            )
        )
        store.save()
        assert "hand-authored, no authors declared" in all_text(
            find(run_directive(payload("out.svg")), "admonition")
        )

    def test_a_break_further_back_in_the_chain_is_not_reported_as_fine(
        self, project: Path
    ):
        """The most misleading thing this panel could print.

        Staleness does not stop at the first link. A composite whose `.drawio`
        is untouched passes every direct check even when the data three steps
        back has moved, because nothing regenerated the panel in between.
        """
        store = Store.load(project)
        (project / "composite.drawio").write_text("<mxfile/>")
        store.record(
            Artifact(
                path="composite.drawio",
                hash=hash_file(project / "composite.drawio"),
                kind="authored",
                inputs=[Input("plot.png", hash_file(project / "plot.png"))],
            )
        )
        store.save()
        assert (
            find(run_directive(payload("composite.drawio")), "admonition")[
                "kind"
            ]
            == "note"
        )

        # plot.png is untouched, so composite.drawio is sound in itself.
        (project / "data.csv").write_text("x,y\n9,9\n")
        panel = find(run_directive(payload("composite.drawio")), "admonition")
        assert panel["kind"] == "danger"
        assert "Out of date upstream" in all_text(panel)
        assert "data.csv" in all_text(panel)
        assert "do not edit figmint.toml" in all_text(panel)

    def test_ai_is_read_from_the_file_when_it_carries_credentials(
        self, project: Path, monkeypatch
    ):
        """No need to restate in the record what the file already asserts.

        A manifest is signed by whoever made the file, so it is the stronger
        evidence; copying it into `figmint.toml` would create a second copy that
        can drift.
        """
        from figmint.declare import declare
        from figmint.origins import Author, attested

        declare(project / "data.csv", attested(Author("A Researcher")))
        monkeypatch.setattr(
            "figmint.myst.credentials_for", lambda path: _creds(True)
        )
        shown = all_text(
            find(run_directive(payload("plot.png")), "admonition")
        )
        assert "per the file's own credentials" in shown

    def test_ai_lives_in_the_record_when_the_format_cannot_carry_it(
        self, project: Path
    ):
        """A `.py` or `.csv` has nowhere to put a manifest, so the record is the
        only place the disclosure can live."""
        from figmint.declare import declare
        from figmint.origins import Author, attested

        declare(
            project / "data.csv",
            attested(Author("A Researcher"), Author("Claude Opus 5", "ai")),
        )
        shown = all_text(
            find(run_directive(payload("plot.png")), "admonition")
        )
        assert "per the record" in shown

    def test_a_stripped_disclosure_is_reported_not_hidden(
        self, project: Path, monkeypatch
    ):
        """The failure the record exists to catch.

        Any tool that re-encodes an image silently discards its manifest. If the
        record deferred to the file, the AI disclosure would evaporate the first
        time someone opened it in an editor.
        """
        from figmint.declare import declare
        from figmint.origins import Author, attested

        declare(
            project / "data.csv",
            attested(Author("A Researcher"), Author("Claude Opus 5", "ai")),
        )
        monkeypatch.setattr(
            "figmint.myst.credentials_for", lambda path: _creds(False)
        )
        shown = all_text(
            find(run_directive(payload("plot.png")), "admonition")
        )
        assert "no longer say so" in shown

    def test_the_command_is_shown(self, project: Path):
        assert "uv run plot.py" in all_text(
            find(run_directive(payload("plot.png")), "admonition")
        )

    def test_a_changed_input_turns_it_red(self, project: Path):
        (project / "data.csv").write_text("x,y\n9,9\n")
        panel = find(run_directive(payload("plot.png")), "admonition")
        assert panel["kind"] == "danger"
        assert "Out of date" in all_text(panel)
        assert "data.csv" in all_text(panel)

    def test_it_says_not_to_edit_the_record(self, project: Path):
        """The one repair a reader in a hurry might try, and the one that
        destroys the evidence."""
        (project / "data.csv").write_text("x,y\n9,9\n")
        panel = find(run_directive(payload("plot.png")), "admonition")
        assert "do not edit figmint.toml" in all_text(panel)

    def test_an_edited_artifact_is_distinguished(self, project: Path):
        (project / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\ntampered")
        panel = find(run_directive(payload("plot.png")), "admonition")
        assert panel["kind"] == "danger"
        assert "Edited outside figmint" in all_text(panel)

    def test_an_unrecorded_artifact_says_so(self, project: Path):
        (project / "other.png").write_bytes(b"x")
        panel = find(run_directive(payload("other.png")), "admonition")
        assert panel["kind"] == "warning"
        assert "Not tracked" in all_text(panel)

    def test_the_panel_can_be_turned_off(self, project: Path):
        nodes = run_directive(payload("plot.png", provenance=False))
        assert find(nodes, "admonition") is None
        assert find(nodes, "container") is not None


class TestDocumentSummary:
    def test_it_lists_every_recorded_artifact(self, project: Path):
        panel = find(
            document_directive({"options": {"table": True}, "node": {}}),
            "admonition",
        )
        assert "plot.png" in all_text(panel)
        assert "1 artifact(s), all up to date" in all_text(panel)

    def test_it_goes_red_when_anything_is_stale(self, project: Path):
        (project / "data.csv").write_text("x,y\n9,9\n")
        panel = find(
            document_directive({"options": {}, "node": {}}), "admonition"
        )
        assert panel["kind"] == "danger"
        assert "out of date" in all_text(panel)

    def test_the_dag_is_drawn(self, project: Path):
        """The record is already a DAG, so the diagram cannot disagree with the
        freshness reported beside it."""
        nodes = document_directive({"options": {"graph": True}, "node": {}})
        diagram = find(nodes, "mermaid")
        assert diagram is not None
        assert "graph LR" in diagram["value"]
        assert "plot.png" in diagram["value"]

    def test_the_dag_can_be_turned_off(self, project: Path):
        nodes = document_directive({"options": {"graph": False}, "node": {}})
        assert find(nodes, "mermaid") is None

    def test_a_stale_artifact_is_marked_in_the_dag(self, project: Path):
        (project / "data.csv").write_text("x,y\n9,9\n")
        diagram = find(
            document_directive({"options": {"graph": True}, "node": {}}),
            "mermaid",
        )
        assert "⚠" in diagram["value"]

    def test_an_empty_project_says_so(self, tmp_path: Path, monkeypatch):
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        panel = find(
            document_directive({"options": {}, "node": {}}), "admonition"
        )
        assert "Nothing recorded" in all_text(panel)


class TestDocumentSelfReference:
    """A document cannot honestly report on its own freshness from inside itself.

    While the page is being written its recorded hash still describes the
    *previous* build, so it shows as out of date every single time — a panel
    that cries wolf permanently, which is worse than no panel.
    """

    def record_document(self, project: Path) -> None:
        (project / "doc.html").write_text("<html>old</html>")
        store = Store.load(project)
        store.record(
            Artifact(
                path="doc.html",
                hash=hash_file(project / "doc.html"),
                command="uv run myst build --html",
                inputs=[Input("plot.png", hash_file(project / "plot.png"))],
            )
        )
        store.save()

    def options(self, **extra) -> dict:
        return {"options": {"artifact": "doc.html", **extra}, "node": {}}

    def test_the_rebuild_command_is_shown(self, project: Path):
        """Taken from the record, so it cannot drift from the command that
        actually produced the file sitting next to it."""
        self.record_document(project)
        panel = find(document_directive(self.options()), "admonition")
        assert "uv run myst build --html" in all_text(panel)

    def test_its_own_output_is_left_out_of_the_tally(self, project: Path):
        self.record_document(project)
        # The document is mid-build: what is on disk is not what was recorded.
        (project / "doc.html").write_text("<html>being written</html>")

        panel = find(document_directive(self.options()), "admonition")
        assert panel["kind"] == "note"
        assert "all up to date" in all_text(panel)

    def test_it_stays_collapsible_when_nothing_is_wrong(self, project: Path):
        self.record_document(project)
        (project / "doc.html").write_text("<html>being written</html>")
        panel = find(document_directive(self.options()), "admonition")
        assert panel["class"] == "dropdown"

    def test_a_real_problem_still_shows(self, project: Path):
        """Excluding the document must not excuse anything else."""
        self.record_document(project)
        (project / "data.csv").write_text("x,y\n9,9\n")
        panel = find(document_directive(self.options()), "admonition")
        assert panel["kind"] == "danger"
        assert panel.get("class") != "dropdown"

    def test_it_says_where_its_own_freshness_is_checked(self, project: Path):
        self.record_document(project)
        panel = find(document_directive(self.options()), "admonition")
        assert "figmint status" in all_text(panel)

    def test_an_unrecorded_document_is_nudged(self, project: Path):
        """Naming an output nothing produced should say so, not stay silent."""
        panel = find(
            document_directive(
                {"options": {"artifact": "nowhere.html"}, "node": {}}
            ),
            "admonition",
        )
        assert "not recorded" in all_text(panel)

    def test_without_the_option_nothing_changes(self, project: Path):
        self.record_document(project)
        panel = find(
            document_directive({"options": {}, "node": {}}), "admonition"
        )
        assert "doc.html" in all_text(find(panel, "table"))


class TestNamingTheCause:
    """A stale figure did not change — its inputs did.

    Using one word for both sends a reader looking for an edit to a file nobody
    touched, and leaves the file they *did* edit unmentioned.
    """

    def build_chain(self, project: Path) -> None:
        """plot.py -> plot.png -> composite."""
        store = Store.load(project)
        (project / "plot.py").write_text("print('draw')")
        artifact = store.get("plot.png")
        artifact.inputs.append(
            Input("plot.py", hash_file(project / "plot.py"), kind="code")
        )
        store.record(artifact)

        (project / "composite.svg").write_text("<svg/>")
        store.record(
            Artifact(
                path="composite.svg",
                hash=hash_file(project / "composite.svg"),
                command="figmint drawio export c.drawio composite.svg",
                inputs=[Input("plot.png", hash_file(project / "plot.png"))],
            )
        )
        store.save()

        from figmint.declare import declare
        from figmint.origins import Author, attested

        declare(project / "plot.py", attested(Author("A Researcher")))

    def test_the_edited_script_is_named(self, project: Path):
        self.build_chain(project)
        (project / "plot.py").write_text("print('draw differently')")

        panel = find(run_directive(payload("composite.svg")), "admonition")
        title = all_text(panel["children"][0])
        assert "plot.py changed" in title

    def test_the_stale_figure_is_not_called_changed(self, project: Path):
        """It is the same bytes it always was."""
        self.build_chain(project)
        (project / "plot.py").write_text("print('draw differently')")

        panel = find(run_directive(payload("composite.svg")), "admonition")
        title = all_text(panel["children"][0])
        assert "plot.png changed" not in title
        assert "plot.png has not been regenerated" in title

    def test_the_table_says_needs_regenerating(self, project: Path):
        self.build_chain(project)
        (project / "plot.py").write_text("print('draw differently')")

        rows = find(
            find(run_directive(payload("composite.svg")), "admonition"),
            "table",
        )["children"]
        by_path = {
            all_text(r["children"][0]): all_text(r["children"][-1])
            for r in rows
        }
        assert by_path["plot.png"] == "⚠️ needs regenerating"
        assert by_path["plot.py"] == "⚠️ changed since"

    def test_a_declared_source_is_still_compared_downstream(
        self, project: Path
    ):
        """`status` does not check a declaration against its own hash, but the
        chain asks a different question that does have an answer: are these the
        bytes the thing downstream was built from?"""
        self.build_chain(project)
        assert check_path(project / "plot.py").state is State.OK

        (project / "plot.py").write_text("print('edited')")
        assert check_path(project / "plot.py").state is State.OK

        chain = {
            i.path: i.state
            for i in chain_items(
                check_path(project / "composite.svg"), Store.load(project)
            )
        }
        assert chain["plot.py"] is State.STALE
