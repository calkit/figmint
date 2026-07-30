"""The MyST plugin: the executable protocol, and what it renders.

The value of these tests is mostly in the protocol. mystmd talks to this plugin
by spawning it and exchanging JSON on stdin/stdout, so a broken spec or a stray
`print` shows up as an opaque "Non-zero exit code" during someone's document
build, with no stack trace. Exercising the real entry point catches that.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from figmint.formats import open_document
from figmint.myst import OPEN_URL_ENV, SPEC, main, run_directive
from figmint.provenance import Level
from figmint.report import inspect_document

EXAMPLES = Path(__file__).parent.parent / "examples"


def directive_payload(target: str, **options) -> dict:
    """The shape mystmd hands a directive, reduced to what the plugin reads."""
    return {
        "name": "figmint",
        "arg": target,
        "options": options,
        "body": "A caption.",
        "node": {
            "type": "mystDirective",
            "name": "figmint",
            "args": target,
            "options": options,
            "children": [
                {
                    "type": "mystDirectiveBody",
                    "value": "A caption.",
                    "children": [
                        {
                            "type": "paragraph",
                            "children": [{"type": "text", "value": "A caption."}],
                        }
                    ],
                }
            ],
        },
    }


def find(node, node_type: str):
    """First node of a type, depth-first."""
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
    # text-only walk would silently miss every command the panel suggests.
    value = node.get("value") if node.get("type") in ("text", "inlineCode") else ""
    return " ".join([value or "", all_text(node.get("children") or [])]).strip()


@pytest.fixture
def figure(tmp_path: Path) -> Path:
    """A diagram with one identified component, in its own project."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "figures").mkdir()
    shutil.copy(EXAMPLES / "figures" / "cp_curve.svg", tmp_path / "figures" / "plot.svg")
    shutil.copy(
        EXAMPLES / "figures" / "cp_curve.svg.prov.yaml",
        tmp_path / "figures" / "plot.svg.prov.yaml",
    )
    target = tmp_path / "figures" / "diagram.drawio"
    from figmint.cli import main as cli

    cli(["place", str(tmp_path / "figures" / "plot.svg"), "--into", str(target), "--create"])
    return target


class TestProtocol:
    """What mystmd actually does to this program."""

    def test_no_arguments_prints_the_spec(self, capsys):
        assert main([]) == 0
        spec = json.loads(capsys.readouterr().out)
        assert spec["name"] == "figmint"
        assert [d["name"] for d in spec["directives"]] == ["figmint"]

    def test_the_spec_is_json_serialisable(self):
        # Enum values or Path objects would sneak past a unit test and fail only
        # when mystmd parses stdout.
        json.dumps(SPEC)

    def test_the_real_executable_answers(self, figure: Path, tmp_path: Path):
        """Spawn it the way mystmd does, rather than calling main() in-process."""
        spec = subprocess.run(
            [sys.executable, "-m", "figmint.myst"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(spec.stdout)["name"] == "figmint"

        result = subprocess.run(
            [sys.executable, "-m", "figmint.myst", "--directive", "figmint"],
            input=json.dumps(directive_payload("figures/diagram.drawio")),
            capture_output=True,
            text=True,
            cwd=tmp_path,
            check=True,
        )
        nodes = json.loads(result.stdout)
        assert find(nodes, "container")["kind"] == "figure"

    def test_an_unknown_request_fails_loudly(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
        assert main(["--role", "nope"]) == 1
        assert "unsupported request" in capsys.readouterr().err


class TestFigureNode:
    """The figure itself must behave exactly like `figure` would."""

    def test_emits_a_figure_container(self, figure: Path, monkeypatch):
        monkeypatch.chdir(figure.parent.parent)
        nodes = run_directive(directive_payload("figures/diagram.drawio"))
        container = find(nodes, "container")
        assert container["kind"] == "figure"

    def test_the_name_option_becomes_a_label(self, figure: Path, monkeypatch):
        monkeypatch.chdir(figure.parent.parent)
        nodes = run_directive(
            directive_payload("figures/diagram.drawio", name="fig-thing")
        )
        container = find(nodes, "container")
        # Lowercased identifier plus original label is what MyST's own figure
        # directive produces, and cross-references depend on it.
        assert container["identifier"] == "fig-thing"
        assert container["label"] == "fig-thing"

    def test_the_caption_keeps_its_parsed_children(self, figure: Path, monkeypatch):
        """Reusing the parsed body is what keeps emphasis and citations working."""
        monkeypatch.chdir(figure.parent.parent)
        nodes = run_directive(directive_payload("figures/diagram.drawio"))
        caption = find(nodes, "caption")
        assert find(caption, "paragraph") is not None
        assert "A caption." in all_text(caption)

    def test_width_is_passed_through(self, figure: Path, monkeypatch):
        monkeypatch.chdir(figure.parent.parent)
        nodes = run_directive(directive_payload("figures/diagram.drawio", width="90%"))
        assert find(nodes, "image")["width"] == "90%"

    def test_the_image_url_is_left_relative(self, figure: Path, monkeypatch):
        """MyST resolves and copies the asset itself; an absolute path breaks that."""
        monkeypatch.chdir(figure.parent.parent)
        nodes = run_directive(directive_payload("figures/diagram.drawio"))
        assert find(nodes, "image")["url"] == "figures/diagram.drawio"


class TestProvenancePanel:
    def test_a_clean_figure_reports_everything_up_to_date(self, figure: Path, monkeypatch):
        monkeypatch.chdir(figure.parent.parent)
        nodes = run_directive(directive_payload("figures/diagram.drawio"))
        panel = find(nodes, "admonition")
        assert panel["kind"] == "note"
        # Collapsed when there is nothing wrong, so a long document does not turn
        # into a wall of metadata.
        assert panel["class"] == "dropdown"
        assert "everything up to date" in all_text(panel)

    def test_the_two_freshness_questions_get_their_own_columns(
        self, figure: Path, monkeypatch
    ):
        """A green cell beside a red banner reads as a contradiction.

        "Matches source" and "the source is current" are different claims, and
        the table has to be able to say the first without implying the second.
        """
        monkeypatch.chdir(figure.parent.parent)
        nodes = run_directive(directive_payload("figures/diagram.drawio"))
        header = find(find(nodes, "admonition"), "tableRow")
        headings = [all_text(c) for c in header["children"]]
        assert headings == ["Component", "Provenance", "Source", "In figure", "Basis"]
        # "current" overclaims for a column that only compares two files.
        assert "current" not in all_text(find(nodes, "admonition")).lower().replace(
            "not generated here", ""
        ).replace("everything up to date", "").replace("up to date", "")

    def test_a_changed_component_turns_it_red(self, figure: Path, monkeypatch):
        monkeypatch.chdir(figure.parent.parent)
        plot = figure.parent / "plot.svg"
        plot.write_text(plot.read_text().replace("</svg>", "<!-- edited --></svg>"))

        nodes = run_directive(directive_payload("figures/diagram.drawio"))
        panel = find(nodes, "admonition")
        assert panel["kind"] == "danger"
        assert "Out of date" in all_text(panel)
        assert "plot.svg" in all_text(panel)
        # And it should say what to do about it.
        assert "make refresh" in all_text(panel)

    def test_an_out_of_date_stage_turns_it_red(self, figure: Path, monkeypatch):
        """The case where nothing about the figure itself looks wrong.

        The embedded copy matches the file on disk exactly. But the file is the
        output of a stage whose script has since been edited, so the picture is
        showing results from code that no longer exists.
        """
        import hashlib

        root = figure.parent.parent
        monkeypatch.chdir(root)
        (root / "calkit.yaml").write_text(
            "pipeline:\n"
            "  stages:\n"
            "    plot-cp:\n"
            "      kind: python-script\n"
            "      script_path: scripts/plot_cp.py\n"
            "      outputs:\n"
            "        - figures/plot.svg\n"
        )
        (root / "scripts").mkdir()
        (root / "scripts" / "plot_cp.py").write_text("# edited since the last run")
        stale = hashlib.md5(b"# what it was when the stage ran").hexdigest()
        (root / "dvc.lock").write_text(
            "schema: '2.0'\nstages:\n  plot-cp:\n    cmd: python scripts/plot_cp.py\n"
            "    deps:\n"
            f"    - path: scripts/plot_cp.py\n      hash: md5\n      md5: {stale}\n"
        )

        nodes = run_directive(directive_payload("figures/diagram.drawio"))
        panel = find(nodes, "admonition")
        assert panel["kind"] == "danger"
        assert "Upstream out of date" in all_text(panel)
        assert "scripts/plot_cp.py" in all_text(panel)
        # Re-embedding would faithfully copy a stale file, so it must not be
        # the suggested fix here.
        assert "calkit run" in all_text(panel)
        assert "make refresh" not in all_text(panel)

        # No green tick anywhere in a row whose provenance is broken. The
        # embedded copy really does match the file, but saying so with a ✅
        # beside a red banner reads as "this panel is fine".
        table_text = all_text(find(panel, "table"))
        assert "⚠️ matches, but the source is stale" in table_text
        assert "✅" not in table_text

        # And the rating has to drop: the panel is no longer reproducible.
        report = inspect_document(figure)
        assert report.components[0].level is Level.GENERATED
        assert report.components[0].upstream_stale
        # The embedded copy really does still match; that is a separate question.
        assert not report.components[0].stale

    def test_a_figure_behind_its_own_source_is_reported(
        self, figure: Path, monkeypatch
    ):
        """Editing the authored diagram must show up, and nothing else can see it.

        Every component is still embedded, still matches its file, still
        identified — the figure is internally perfect. What changed is the
        diagram it was rendered *from*, so the picture on the page is a
        rendering of something that no longer exists. Checks that look inside
        the figure are structurally blind to this.
        """
        import hashlib

        root = figure.parent.parent
        monkeypatch.chdir(root)
        rendered = figure.with_name("diagram.drawio.svg")
        shutil.copy(figure, rendered)

        (root / "calkit.yaml").write_text(
            "pipeline:\n"
            "  stages:\n"
            "    embed:\n"
            "      kind: command\n"
            "      command: figmint reimport\n"
            "      inputs:\n"
            "        - figures/diagram.drawio\n"
            "      outputs:\n"
            "        - .build/diagram.drawio\n"
            "    render:\n"
            "      kind: command\n"
            "      command: drawio -x\n"
            "      inputs:\n"
            "        - from_stage_outputs: embed\n"
            "      outputs:\n"
            "        - figures/diagram.drawio.svg\n"
        )
        # The lock remembers a *different* authored diagram. Note that `render`
        # itself looks perfectly current: its only input is the intermediate,
        # which `embed` has not rewritten yet. The staleness is one stage back.
        stale = hashlib.md5(b"an older diagram").hexdigest()
        build = hashlib.md5(
            (root / ".build" / "diagram.drawio").read_bytes()
            if (root / ".build" / "diagram.drawio").is_file()
            else b""
        ).hexdigest()
        (root / ".build").mkdir(exist_ok=True)
        (root / ".build" / "diagram.drawio").write_text("intermediate")
        build = hashlib.md5(b"intermediate").hexdigest()
        (root / "dvc.lock").write_text(
            "schema: '2.0'\nstages:\n"
            "  embed:\n    cmd: figmint reimport\n    deps:\n"
            f"    - path: figures/diagram.drawio\n      hash: md5\n      md5: {stale}\n"
            "  render:\n    cmd: drawio -x\n    deps:\n"
            f"    - path: .build/diagram.drawio\n      hash: md5\n      md5: {build}\n"
        )

        report = inspect_document(rendered)
        assert report.behind_source
        assert report.stale
        assert report.document_stage == "render"
        # Blame the stage that is actually out of date, not the one asked about.
        assert report.document_stage_blamed == "embed"
        assert report.document_stage_changed == ("figures/diagram.drawio",)
        # Components are untouched by this; the figure is stale for a different
        # reason entirely.
        assert not any(c.stale for c in report.components)

        nodes = run_directive(directive_payload("figures/diagram.drawio.svg"))
        panel = find(nodes, "admonition")
        assert panel["kind"] == "danger"
        assert "out of date" in all_text(panel)
        assert "figures/diagram.drawio" in all_text(panel)

    def test_unaccounted_input_data_is_reported(self, figure: Path, monkeypatch):
        """The gap every other check is structurally unable to see.

        Each component can be identified, current, and produced by a verified
        pipeline stage, and the whole chain can still rest on a CSV that
        appeared one day. The other checks all look at components; this looks
        one link further back.
        """
        root = figure.parent.parent
        monkeypatch.chdir(root)
        (root / "data").mkdir()
        (root / "data" / "raw.csv").write_text("a,b\n1,2\n")
        (root / "calkit.yaml").write_text(
            "datasets:\n"
            "  raw:\n"
            "    path: data/raw.csv\n"
            "    title: Some measurements\n"  # documentation, not provenance
            "pipeline:\n"
            "  stages:\n"
            "    plot:\n"
            "      kind: python-script\n"
            "      script_path: scripts/plot.py\n"
            "      inputs:\n"
            "        - data/raw.csv\n"
            "      outputs:\n"
            "        - figures/plot.svg\n"
        )

        nodes = run_directive(directive_payload("figures/diagram.drawio"))
        panel = find(nodes, "admonition")
        assert "data/raw.csv" in all_text(panel)
        assert "no stated origin" in all_text(panel) or "Unaccounted" in all_text(panel)

        report = inspect_document(figure)
        assert report.unaccounted_inputs == ("data/raw.csv",)
        # A warning, not a violation: the components themselves are fine, and
        # failing here would punish projects that adopted a pipeline at all.
        assert report.publishable

    def test_an_imported_from_declaration_accounts_for_input_data(
        self, figure: Path, monkeypatch
    ):
        root = figure.parent.parent
        monkeypatch.chdir(root)
        (root / "data").mkdir()
        (root / "data" / "raw.csv").write_text("a,b\n1,2\n")
        (root / "calkit.yaml").write_text(
            "datasets:\n"
            "  raw:\n"
            "    path: data/raw.csv\n"
            "    title: Some measurements\n"
            "    imported_from:\n"
            "      url: https://example.org/raw.csv\n"
            "pipeline:\n"
            "  stages:\n"
            "    plot:\n"
            "      kind: python-script\n"
            "      script_path: scripts/plot.py\n"
            "      inputs:\n"
            "        - data/raw.csv\n"
            "      outputs:\n"
            "        - figures/plot.svg\n"
        )
        assert inspect_document(figure).unaccounted_inputs == ()

    def test_a_missing_component_is_reported(self, figure: Path, monkeypatch):
        monkeypatch.chdir(figure.parent.parent)
        (figure.parent / "plot.svg").unlink()
        nodes = run_directive(directive_payload("figures/diagram.drawio"))
        assert find(nodes, "admonition")["kind"] == "danger"

    def test_the_panel_can_be_turned_off(self, figure: Path, monkeypatch):
        monkeypatch.chdir(figure.parent.parent)
        nodes = run_directive(
            directive_payload("figures/diagram.drawio", provenance=False)
        )
        assert find(nodes, "admonition") is None
        assert find(nodes, "container") is not None

    def test_without_an_opener_it_names_the_file_rather_than_linking(
        self, figure: Path, monkeypatch
    ):
        """No link at all beats either alternative here.

        A `vscode://` link is dead in VS Code's Simple Browser, which is a
        webview and will not follow a non-http scheme. A markdown link to the
        source is worse: MyST copies a linked project file into the build under
        a content-hashed name, so clicking it hands the reader a duplicate, and
        editing that duplicate is lost work.
        """
        monkeypatch.delenv(OPEN_URL_ENV, raising=False)
        monkeypatch.chdir(figure.parent.parent)
        nodes = run_directive(directive_payload("figures/diagram.drawio"))
        panel = find(nodes, "admonition")
        assert find(panel, "link") is None
        assert "figures/diagram.drawio" in all_text(panel)

    def test_it_does_not_assume_the_project_has_a_makefile(
        self, figure: Path, monkeypatch
    ):
        """`make edit` is one project's idiom, not a figmint convention."""
        monkeypatch.delenv(OPEN_URL_ENV, raising=False)
        root = figure.parent.parent
        monkeypatch.chdir(root)
        (root / "Makefile").write_text("edit:\n\tdrawio figures/diagram.drawio\n")
        nodes = run_directive(directive_payload("figures/diagram.drawio"))
        assert "make edit" not in all_text(find(nodes, "admonition"))

    def test_with_an_opener_it_emits_a_clickable_http_link(
        self, figure: Path, monkeypatch
    ):
        """http is the only scheme a webview will follow, so this is the one
        form of the button that works inside the editor's own preview."""
        monkeypatch.setenv(OPEN_URL_ENV, "http://127.0.0.1:8765/open")
        monkeypatch.chdir(figure.parent.parent)
        nodes = run_directive(directive_payload("figures/diagram.drawio"))
        link = find(find(nodes, "admonition"), "link")
        assert link["url"] == (
            "http://127.0.0.1:8765/open?path=figures/diagram.drawio"
        )

    def test_the_link_prefers_the_authored_source(self, figure: Path, monkeypatch):
        """A rendered `.drawio.svg` is a build artifact; editing it is a trap.

        The next pipeline run overwrites it, so when an authored `.drawio` sits
        beside it that is what the edit button must open.
        """
        monkeypatch.setenv(OPEN_URL_ENV, "http://127.0.0.1:8765/open")
        root = figure.parent.parent
        monkeypatch.chdir(root)
        rendered = figure.with_name("diagram.drawio.svg")
        shutil.copy(figure, rendered)

        nodes = run_directive(directive_payload("figures/diagram.drawio.svg"))
        link = find(find(nodes, "admonition"), "link")
        assert link["url"].endswith("?path=figures/diagram.drawio")
        # The image still shows the rendered file — only the edit target moves.
        assert find(nodes, "image")["url"] == "figures/diagram.drawio.svg"

    def test_the_editor_link_can_be_turned_off(self, figure: Path, monkeypatch):
        monkeypatch.chdir(figure.parent.parent)
        nodes = run_directive(directive_payload("figures/diagram.drawio", editor=False))
        assert find(find(nodes, "admonition"), "link") is None

    def test_a_broken_figure_does_not_raise(self, tmp_path: Path, monkeypatch):
        """A build must not die because one figure is unreadable."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "nope.drawio").write_text("not xml at all")
        nodes = run_directive(directive_payload("nope.drawio"))
        assert find(nodes, "admonition")["kind"] == "danger"
        assert find(nodes, "container") is not None


class TestExampleDocument:
    """The shipped example, which is also the thing most likely to rot."""

    example = EXAMPLES / "myst"

    @pytest.mark.skipif(
        not (EXAMPLES / "myst" / "figures" / "composite.drawio.svg").exists(),
        reason="example figure not built",
    )
    def test_the_example_figure_is_identified(self):
        report = inspect_document(self.example / "figures" / "composite.drawio.svg")
        assert report.components, "expected the composite to declare components"
        assert report.publishable
        # An AI-generated panel is only disclosed if the credentials are read.
        assert any(
            (c.credentials or {}).get("machineGenerated") for c in report.components
        )

    def test_the_document_uses_the_directive(self):
        text = (self.example / "index.md").read_text()
        assert ":::{figmint}" in text

    def test_the_plugin_is_registered(self):
        text = (self.example / "myst.yml").read_text()
        assert "figmint-myst" in text
        assert "type: executable" in text

    def test_source_and_output_are_different_files(self):
        """The pipeline cannot build a file from itself; see calkit.yaml."""
        text = (self.example / "calkit.yaml").read_text()
        assert "figmint reimport figures/composite.drawio -o" in text
        assert "-o figures/composite.drawio.svg .build/composite.drawio" in text


class TestReportLayer:
    """`figmint check` and the plugin have to agree, because they share this."""

    def test_levels_and_freshness_come_back_together(self, figure: Path):
        report = inspect_document(figure)
        assert len(report.components) == 1
        component = report.components[0]
        assert component.level is Level.GENERATED
        assert not component.stale
        assert component.permitted

    def test_weakest_is_the_floor_not_the_average(self, figure: Path):
        from figmint.cli import main as cli

        # A second, anonymous component drags the whole figure down.
        anonymous = figure.parent / "mystery.svg"
        shutil.copy(EXAMPLES / "figures" / "cp_curve.svg", anonymous)
        cli(["place", str(anonymous), "--into", str(figure), "--force"])

        report = inspect_document(figure)
        assert len(report.components) == 2
        assert report.weakest is Level.UNIDENTIFIED
        assert not report.publishable

    def test_an_unreadable_document_reports_an_error(self, tmp_path: Path):
        broken = tmp_path / "broken.drawio"
        broken.write_text("<<<")
        report = inspect_document(broken)
        assert report.error
        assert report.components == []

    def test_it_is_json_serialisable(self, figure: Path):
        json.dumps(inspect_document(figure).to_dict())

    def test_components_match_the_document(self, figure: Path):
        keys = [c.key for c in open_document(figure).components()]
        assert [c.key for c in inspect_document(figure).components] == keys
