"""The Quarto extension: the executable protocol, and what it hands back.

The value is mostly in the protocol and in the split. `fromwhere.lua` spawns
this program and exchanges JSON with it, so a broken spec or a stray `print` on
stdout shows up as a document that quietly loses every provenance panel. The
Lua half is not exercised here — it needs Quarto — so what these tests hold
down is the contract it consumes: the three display kinds, the body/panel
split, and the fact that a path is resolved against the *document* rather than
against whatever directory Quarto happened to start in.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from fromwhere.quarto import (
    EXTENSION_FILES,
    SPEC,
    install_extension,
    main,
    run_directive,
)
from fromwhere.store import Artifact, Input, Store, hash_file


def payload(target: str | None = None, base: Path | None = None, **options):
    """The shape the Lua filter sends, reduced to what this program reads."""
    if target is not None:
        options["src"] = target
    data: dict = {"options": options}
    if base is not None:
        data["base"] = str(base)
    return data


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
    value = (
        node.get("value") if node.get("type") in ("text", "inlineCode") else ""
    )
    return " ".join(
        [value or "", all_text(node.get("children") or [])]
    ).strip()


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "data.csv").write_text("x,y\n1,2\n")
    (tmp_path / "uv.lock").write_text("lock")
    (tmp_path / "notes.txt").write_text("nothing to draw")
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
    # Declared, so the fixture's default state is a clean one. Without this
    # every panel here reports incomplete provenance — correctly, and
    # unhelpfully, since almost none of these tests are about that.
    from fromwhere.declare import declare
    from fromwhere.origins import Author, attested

    declare(tmp_path / "data.csv", attested(Author("A Researcher")))
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestProtocol:
    def test_there_is_one_directive(self, capsys):
        """Two of them made one idea look like two features."""
        assert main([]) == 0
        spec = json.loads(capsys.readouterr().out)
        assert [d["name"] for d in spec["directives"]] == ["fromwhere"]
        # Both scopes' options are declared together: Quarto passes every
        # attribute through, and a reader moving `artifact` onto a block that
        # already has `rows` should not have to know which list it came from.
        options = spec["directives"][0]["options"]
        assert {"src", "kind", "rows", "artifact", "graph"} <= set(options)
        # An enum or a Path would sneak past a unit test and fail only when the
        # filter parses stdout.
        json.dumps(SPEC)

    def test_the_real_executable_answers(self, project: Path):
        spec = subprocess.run(
            [sys.executable, "-m", "fromwhere.quarto"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(spec.stdout)["name"] == "fromwhere"

        for sent, expected in (
            (payload("plot.png"), "figure"),
            (payload(), "document"),
        ):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "fromwhere.quarto",
                    "--directive",
                    "fromwhere",
                ],
                input=json.dumps(sent),
                capture_output=True,
                text=True,
                cwd=project,
                check=True,
            )
            answer = json.loads(result.stdout)
            assert set(answer) == {"kind", "body", "panel"}
            assert answer["kind"] == expected
            assert find(answer["panel"], "admonition") is not None

    def test_an_unknown_request_fails_loudly(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
        assert main(["--role", "nope"]) == 1
        assert "unsupported request" in capsys.readouterr().err


class TestArtifactBlock:
    def test_the_three_display_kinds(self, project: Path):
        """A picture, a table, and a thing that is neither.

        The kind is what the Lua half switches on: only a figure gets wrapped
        so Quarto will number it, and an `image` node pointing at a `.txt`
        would put a broken picture in the page.
        """
        figure = run_directive(payload("plot.png"))
        assert figure["kind"] == "figure"
        assert figure["body"][0]["type"] == "image"
        # Left relative: Quarto resolves and copies the asset itself.
        assert figure["body"][0]["url"] == "plot.png"

        table = run_directive(payload("data.csv"))
        assert table["kind"] == "table"
        assert find(table["body"], "image") is None
        shown = all_text(find(table["body"], "table"))
        assert "x" in shown and "y" in shown  # header
        assert "1" in shown and "2" in shown  # the row

        other = run_directive(payload("notes.txt"))
        assert other["kind"] == "artifact"
        assert find(other["body"], "image") is None
        assert "notes.txt" in all_text(other["body"])

    def test_an_html_artifact_is_embedded_not_named(self, project: Path):
        """An interactive chart is a figure, not a filename.

        It is also the case where the record earns the most: HTML cannot carry
        Content Credentials in either direction, so the panel is the only
        provenance the figure has.
        """
        (project / "chart.html").write_text("<html></html>")
        result = run_directive(payload("chart.html", height="400px"))
        assert result["kind"] == "interactive"
        assert result["body"][0]["type"] == "embed"
        assert result["body"][0]["url"] == "chart.html"
        assert result["body"][0]["height"] == "400px"
        # Still called a figure, because that is what a reader sees.
        assert "Figure is not tracked" in all_text(result["panel"])

    def test_the_panel_is_named_in_the_readers_terms(self, project: Path):
        """Calling a table a figure is a small lie in the one place the panel
        is meant to be exact."""
        from fromwhere.declare import declare
        from fromwhere.origins import Author, attested

        declare(project / "data.csv", attested(Author("A Researcher")))
        assert "Table is up to date" in all_text(
            run_directive(payload("data.csv"))["panel"]
        )
        assert "Figure is up to date" in all_text(
            run_directive(payload("plot.png"))["panel"]
        )

    def test_image_options_are_carried_through(self, project: Path):
        image = run_directive(
            payload("plot.png", width="70%", align="center", alt="a plot")
        )["body"][0]
        assert image["width"] == "70%"
        assert image["align"] == "center"
        assert image["alt"] == "a plot"

    def test_the_panel_can_be_turned_off(self, project: Path):
        """Every div attribute arrives as a string, so "false" has to mean it."""
        assert run_directive(payload("plot.png"))["panel"]
        assert run_directive(payload("plot.png", provenance="true"))["panel"]
        assert not run_directive(payload("plot.png", provenance="false"))[
            "panel"
        ]

    def test_kind_overrides_what_the_extension_says(self, project: Path):
        """For the `.dat` that is really delimited, and the diagram of the
        method that has no business being numbered as a figure."""
        (project / "readings.dat").write_text("x,y\n3,4\n")
        forced = run_directive(payload("readings.dat", kind="table"))
        assert forced["kind"] == "table"
        assert "3" in all_text(find(forced["body"], "table"))

        plain = run_directive(payload("plot.png", kind="artifact"))
        assert plain["kind"] == "artifact"
        assert find(plain["body"], "image") is None
        assert "Artifact" in all_text(plain["panel"])

        # Presentation, not a provenance claim: a value nobody recognises
        # falls back to the guess rather than failing the build.
        assert (
            run_directive(payload("plot.png", kind="picture"))["kind"]
            == "figure"
        )

    def test_long_tables_are_truncated(self, project: Path):
        (project / "big.csv").write_text(
            "x\n" + "".join(f"{n}\n" for n in range(100))
        )
        result = run_directive(payload("big.csv", rows=3))
        rows = find(result["body"], "table")["children"]
        assert len(rows) == 4  # header plus three
        assert "First 3 rows" in all_text(result["body"])

    def test_a_stale_artifact_is_reported_as_such(self, project: Path):
        (project / "data.csv").write_text("x,y\n9,9\n")
        panel = run_directive(payload("plot.png"))["panel"]
        assert find(panel, "admonition")["kind"] == "danger"
        assert "out of date" in all_text(panel)


class TestDocumentBlock:
    def test_naming_no_artifact_describes_the_document(self, project: Path):
        """The scope is the argument: no `src`, and the panel is about the
        page you are reading. `kind` says it outright for anyone who would
        rather not rely on an absence."""
        for sent in (payload(), payload(kind="document")):
            result = run_directive(sent)
            assert result["kind"] == "document"
            assert result["body"] == []
            panel = find(result["panel"], "admonition")
            assert "Document is up to date" in all_text(panel)
            assert "plot.png" in all_text(find(panel, "table"))
            assert find(panel, "mermaid") is not None

    def test_its_own_output_is_left_out_of_the_tally(self, project: Path):
        """A document cannot honestly report on itself from the inside: while
        the page is being written its recorded hash describes the previous
        build, so the panel would cry wolf on every render."""
        store = Store.load(project)
        store.record(
            Artifact(
                path="_site/index.html",
                hash="sha256:whatever-it-was-last-time",
                command="uv run quarto render",
                inputs=[Input("plot.png", hash_file(project / "plot.png"))],
            )
        )
        store.save()
        (project / "_site").mkdir()
        (project / "_site" / "index.html").write_text("a newer build")

        without = all_text(run_directive(payload())["panel"])
        assert "out of date" in without

        with_it = all_text(
            run_directive(payload(artifact="_site/index.html"))["panel"]
        )
        assert "Document is up to date" in with_it
        # Named, so the panel can still say how to rebuild it.
        assert "uv run quarto render" in with_it


class TestPathResolution:
    def test_paths_are_resolved_against_the_document(
        self, project: Path, tmp_path: Path, monkeypatch
    ):
        """Quarto resolves a relative path against the file it appears in.

        Rendering happens from the project root, which for a document in a
        subdirectory is not the same place — and without `base` every artifact
        in it would be looked up somewhere it is not.
        """
        elsewhere = tmp_path.parent / "elsewhere"
        elsewhere.mkdir(exist_ok=True)
        monkeypatch.chdir(elsewhere)

        assert "not tracked" in all_text(
            run_directive(payload("plot.png"))["panel"]
        )
        assert "Figure is up to date" in all_text(
            run_directive(payload("plot.png", base=project))["panel"]
        )


class TestExtensionInstall:
    def test_it_writes_a_usable_extension(self, tmp_path: Path):
        target = install_extension(tmp_path)
        assert target == tmp_path / "_extensions" / "fromwhere"
        for name in EXTENSION_FILES:
            assert (target / name).is_file()
        # The filter has to run before Quarto's own or the callouts are grey
        # boxes and the figures are unnumbered; nothing else records that.
        assert "fromwhere.lua" in (target / "_extension.yml").read_text()
        assert "pre-ast" in (target / "_extension.yml").read_text()

        # Installing twice is how a project picks up a new fromwhere.
        install_extension(tmp_path)
        assert (target / "fromwhere.lua").is_file()
