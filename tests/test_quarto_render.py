"""The Quarto extension, rendered by Quarto.

Everything else about this plugin can be tested by calling Python. The half
that cannot is the half most likely to break: the Lua filter, the position it
runs at, and the assumptions it makes about what Quarto will do with the nodes
it hands over. None of that fails loudly — a filter at the wrong position still
renders, it just produces a grey box under an unnumbered picture, and a
provenance panel that quietly stops saying anything is the one failure this
tool must not have.

So these tests build a small project and run the real `quarto render` on it.
They are skipped where Quarto is not installed, which keeps them from being a
build dependency, and they are the only place the two halves are seen talking
to each other.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from figmint.declare import declare
from figmint.origins import Author, attested
from figmint.quarto import install_extension
from figmint.store import Artifact, Input, Store, hash_file

quarto = pytest.mark.skipif(
    shutil.which("quarto") is None, reason="quarto is not installed"
)

DOCUMENT = """\
---
title: A document with provenance
---

::: {.figmint src="plot.png" #fig-cp width="60%"}
The caption, with a [link](https://example.com).
:::

Referenced as [@fig-cp].

::: {.figmint src="data.csv" #tbl-cp}
The measurements.
:::

::: {.figmint artifact="_site/doc.html"}
:::
"""

CONFIG = """\
project:
  type: default
  output-dir: _site
filters:
  - at: pre-ast
    path: figmint
format:
  html:
    theme: cosmo
"""


def executable() -> Path | None:
    """The `figmint-quarto` console script beside the interpreter running us."""
    scripts = Path(sys.executable).parent
    for name in ("figmint-quarto", "figmint-quarto.exe"):
        if (scripts / name).is_file():
            return scripts / name
    return None


@pytest.fixture
def document(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "data.csv").write_text("tsr,cp\n1.0,0.02\n2.0,0.31\n")
    (tmp_path / "uv.lock").write_text("lock")
    (tmp_path / "plot.py").write_text("# draws the figure\n")
    (tmp_path / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\nplot")
    (tmp_path / "doc.qmd").write_text(DOCUMENT)
    (tmp_path / "_quarto.yml").write_text(CONFIG)

    store = Store.load(tmp_path)
    store.record(
        Artifact(
            path="plot.png",
            hash=hash_file(tmp_path / "plot.png"),
            command="uv run python plot.py",
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
    store.save()
    # Declared, so the fixture's default state is a clean one: without this
    # every panel reports incomplete provenance, correctly and unhelpfully.
    for name in ("data.csv", "plot.py"):
        declare(tmp_path / name, attested(Author("A Researcher")))
    install_extension(tmp_path)
    return tmp_path


def render(project: Path) -> str:
    """Render the project and hand back the page, or fail saying why."""
    program = executable()
    if program is None:
        pytest.skip("figmint-quarto is not installed in this environment")

    environment = dict(os.environ)
    # Named outright rather than left to the filter's search: the test
    # environment is not on PATH and has no .venv beside the document.
    environment["FIGMINT_QUARTO"] = str(program)
    result = subprocess.run(
        ["quarto", "render"],
        cwd=project,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (project / "_site" / "doc.html").read_text(encoding="utf-8")


@quarto
class TestRendering:
    def test_the_panel_is_built_by_quartos_own_machinery(self, document: Path):
        """Position, callout, numbering, diagram — all of it at once.

        Each of these is what the filter running at `pre-ast` buys, and each
        fails silently on its own: an unnumbered figure still renders, and a
        callout Quarto never processed is just a div with a colour class.
        """
        page = render(document)

        # A real Quarto callout, collapsed because nothing is wrong.
        assert 'class="callout callout-style-default callout-note' in page
        assert 'title="🌿 Figure is up to date"' in page
        assert "callout-collapse collapse" in page

        # Numbered and cross-referenced, which only happens if the figure node
        # existed before Quarto's crossref filter ran.
        assert "Figure&nbsp;1" in page
        assert 'class="quarto-xref"' in page
        assert "Table&nbsp;1" in page

        # The chain, and the environment counted as part of it.
        assert "data.csv" in page and "plot.py" in page and "uv.lock" in page
        assert "environment lock" in page
        assert "uv run python plot.py" in page

        # The derivation graph, in the markup Quarto's own Mermaid looks for.
        assert '<pre class="mermaid mermaid-js">' in page
        assert "quarto-diagram/mermaid.min.js" in page
        # Routed through the script rather than fanning into the figure. The
        # arrows are HTML-escaped, which is what Quarto's own diagrams do too.
        assert "data_csv --&gt; plot_py" in page
        assert "uv_lock --&gt; plot_py" in page
        assert "plot_py --&gt; plot_png" in page

    def test_editing_the_script_turns_the_panel_red(self, document: Path):
        """The whole point of the plugin.

        A figure whose plotting script has been rewritten is out of date, and
        the document has to say so — naming the script, because "out of date"
        without a cause sends a reader to open the record and work it out.
        """
        assert 'title="🌿 Figure is up to date"' in render(document)

        (document / "plot.py").write_text("# draws it differently now\n")
        page = render(document)

        assert 'class="callout callout-style-default callout-important' in page
        assert "Figure is out of date" in page
        assert "plot.py changed since it was made" in page
        # Not collapsed: a panel nobody opens is a panel nobody reads.
        assert 'title="🌿 Figure is up to date"' not in page
        # And the document panel agrees, from one level up.
        assert "Document is out of date" in page
        assert "do not edit figmint.toml" in page

    def test_changing_the_data_names_the_data(self, document: Path):
        """The other input, so the panel is not just reporting the last thing
        that happened to be checked."""
        (document / "data.csv").write_text("tsr,cp\n1.0,0.99\n")
        page = render(document)
        assert "Figure is out of date" in page
        assert "data.csv changed since it was made" in page

    def test_the_retired_class_says_what_replaced_it(self, document: Path):
        """An unknown class is just a div to Pandoc: it renders, the panel
        vanishes, and nothing is wrong enough to report. Silence is the one
        failure this tool cannot have, so the block says so in the page."""
        (document / "doc.qmd").write_text(
            DOCUMENT + "\n::: {.figmint-provenance}\n:::\n"
        )
        page = render(document)
        assert "was replaced by" in page
        assert 'class="callout callout-style-default callout-important' in page

    def test_an_interactive_figure_survives_quartos_float_machinery(
        self, document: Path
    ):
        """The frame has to be a raw *inline*.

        Quarto rebuilds a figure's content when it numbers it and discards raw
        blocks on the way through, which renders as an empty box under a
        perfectly correct caption — the failure that looks like a broken figure
        and reads like a missing one.
        """
        (document / "chart.html").write_text("<html><body>hi</body></html>")
        (document / "doc.qmd").write_text(
            DOCUMENT
            + '\n::: {.figmint src="chart.html" #fig-live height="400px"}\n'
            + "An interactive chart.\n:::\n"
        )
        page = render(document)
        assert '<iframe src="chart.html"' in page
        assert "height:400px" in page
        # Numbered like any other figure — the second in this document — and
        # its panel present.
        assert "Figure&nbsp;2" in page
        assert "Figure is not tracked" in page

    def test_an_undeclared_input_turns_the_panel_amber(self, document: Path):
        """Every hash matches and something is still unaccounted for.

        The state most at risk of being rendered as a green tick, because
        nothing automated is failing — which is exactly what makes the gap easy
        to miss.
        """
        # Take the declaration away: the file did not change, only the record's
        # account of who is answerable for it.
        store = Store.load(document)
        del store.artifacts["plot.py"]
        store.save()
        page = render(document)

        assert 'class="callout callout-style-default callout-warning' in page
        assert "Figure has incomplete provenance" in page
        assert "nothing accounts for plot.py" in page
        assert "figmint declare plot.py --mine" in page
        # And the document panel agrees rather than reporting all clear over
        # the top of it.
        assert "Document has incomplete provenance" in page
        assert 'title="🌿 Figure is up to date"' not in page

    def test_an_untracked_artifact_says_so(self, document: Path):
        """Nothing recorded for it at all: the panel has to be honest about
        that rather than rendering an empty box that reads as approval."""
        (document / "loose.png").write_bytes(b"\x89PNG\r\n\x1a\nloose")
        (document / "doc.qmd").write_text(
            DOCUMENT + '\n::: {.figmint src="loose.png"}\n:::\n'
        )
        page = render(document)
        assert "Figure is not tracked" in page
