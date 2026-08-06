"""A Quarto extension that renders an artifact together with what is known
about it.

This is the MyST plugin's twin, and deliberately so. Quarto embeds a figure
perfectly well on its own; what it cannot do is answer the questions a reader
of a research document actually has — what was this made from, is any of it
machine-generated, and is it still consistent with the files behind it. The
answers live in `provenance.toml` and in the artifact's own Content
Credentials, and without something like this they stop at the command line
where only the author ever saw them.

A fenced div does the work of MyST's directive, because it is the one Quarto
construct that takes an argument, options, and a *parsed* caption:

    ::: {.fromwhere src="figures/composite.svg" #fig-performance width="95%"}
    Power coefficient against tip speed ratio.
    :::

Name no `src` and it describes the document instead — the same question at a
wider scope, not a second feature:

    ::: {.fromwhere artifact="_site/index.html"}
    :::

The split between this module and `fromwhere.lua` is not arbitrary. Everything
that decides *what to say* — the freshness chain, the AI disclosure, the
headline — is Python, shared with the MyST plugin down to the node builders, so
the two documents cannot drift apart or from `fromwhere status`. The Lua half
only translates those nodes into Pandoc's AST and hands them to Quarto, which
is the one thing Python cannot do from outside the render.

The protocol matches `fromwhere-myst`: called with no arguments it prints its
specification, and called with `--directive <name>` it reads a JSON payload on
stdin and writes JSON on stdout.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .myst import (
    EMBED_HEIGHT,
    NOUN,
    TABLE_ROW_LIMIT,
    TABULAR,
    as_bool,
    code,
    data_table,
    display_kind,
    paragraph,
    render,
    wants_document,
)
from .myst import (
    document_directive as myst_document_directive,
)
from .status import check_path

#: One block, two scopes — see the note on `myst.SPEC`. Here the scope is the
#: `src` attribute: name a file and the panel is about that file, name none and
#: it is about the document.
SPEC: dict[str, Any] = {
    "name": "fromwhere",
    "author": "fromwhere",
    "license": "MIT",
    "directives": [
        {
            "name": "fromwhere",
            "doc": (
                "Show what an artifact was made from and whether it is still "
                "current. With no `src`, the document itself."
            ),
            "options": {
                "src": {
                    "type": "string",
                    "doc": (
                        "Path to the artifact, relative to the document. Omit "
                        "it for a panel about the whole document."
                    ),
                },
                "kind": {
                    "type": "string",
                    "doc": (
                        "document, figure, table, or artifact. Defaults to "
                        "document when no src is given, and otherwise to "
                        "whatever the file extension says."
                    ),
                },
                "width": {
                    "type": "string",
                    "doc": "Rendered width, as on any Quarto image.",
                },
                "height": {
                    "type": "string",
                    "doc": (
                        "Frame height for an interactive figure, e.g. 420px."
                    ),
                },
                "align": {"type": "string", "doc": "left, center, or right."},
                "alt": {"type": "string", "doc": "Alt text."},
                "rows": {
                    "type": "number",
                    "doc": (
                        "For tabular data: rows to show before truncating "
                        "(default 25)."
                    ),
                },
                "provenance": {
                    "type": "boolean",
                    "doc": "Show the provenance panel. Defaults to true.",
                },
                "table": {
                    "type": "boolean",
                    "doc": "Document panel: show the artifact table.",
                },
                "graph": {
                    "type": "boolean",
                    "doc": (
                        "Document panel: draw the derivation DAG. Defaults to "
                        "true."
                    ),
                },
                "direction": {
                    "type": "string",
                    "doc": "Mermaid direction: LR (default), TD, RL, BT.",
                },
                "artifact": {
                    "type": "string",
                    "doc": (
                        "Document panel: this document's own output(s), comma "
                        "separated. Named so the panel can show how to rebuild "
                        "them and leave them out of its own freshness tally."
                    ),
                },
            },
        },
    ],
}


#: Where a Quarto project keeps its extensions. Quarto looks nowhere else.
EXTENSION_DIR = Path("_extensions") / "fromwhere"

#: The files that make up the extension, shipped inside the package.
EXTENSION_FILES = ("_extension.yml", "fromwhere.lua")

#: What a project has to add to `_quarto.yml` for the filter to run, and where
#: in the pipeline it has to run. Printed by the installer rather than left in
#: a README somebody has to find.
FILTER_CONFIG = """\
filters:
  - at: pre-ast
    path: fromwhere\
"""


def install_extension(destination: Path) -> Path:
    """Copy the Quarto extension into a project.

    `quarto add` would fetch the extension from a repository, at whatever
    version happens to be tagged there. The Lua filter and `fromwhere-quarto`
    speak a protocol private to fromwhere, so the pair has to move together —
    installing from the package that provides the executable is what makes
    that true by construction rather than by asking anyone to keep two
    versions in step.
    """
    from importlib.resources import files

    source = files(__package__) / "quarto_ext"
    target = Path(destination) / EXTENSION_DIR
    target.mkdir(parents=True, exist_ok=True)
    for name in EXTENSION_FILES:
        (target / name).write_bytes((source / name).read_bytes())
    return target


def _relocate(data: dict[str, Any]) -> None:
    """Work from the document's directory, as the paths in it are written.

    Quarto resolves a relative path against the file it appears in, and so must
    fromwhere, or a document in a subdirectory would look up an artifact that
    is not there. The MyST plugin gets this from mystmd's own working
    directory; here the Lua half sends it, and moving into it means every path
    in the shared rendering code means the same thing under both.
    """
    base = data.get("base")
    if base and Path(base).is_dir():
        os.chdir(base)


def run_directive(data: dict[str, Any]) -> dict[str, Any]:
    """One block, two scopes: a named artifact, or the document itself.

    The body and the panel are handed back separately because only the Lua half
    can finish the body — a Quarto figure carries the caption and the
    `#fig-...` label that make it numbered and cross-referenceable, and those
    arrive from the document rather than from here.
    """
    _relocate(data)
    # `wants_document` reads the argument from `arg`, which is where MyST puts
    # the path; Quarto puts it in `src`. Copied across rather than given its own
    # rule, so there is one definition of "no artifact was named".
    options = data.get("options") or {}
    target = str(options.get("src") or "").strip()
    if wants_document({"arg": target, "options": options}):
        return {
            "kind": "document",
            "body": [],
            "panel": myst_document_directive(data),
        }

    source = Path(target)
    if not source.is_absolute():
        source = Path.cwd() / source

    kind = display_kind(target, options.get("kind"))
    body: list[dict[str, Any]] = []
    if kind == "figure":
        image: dict[str, Any] = {"type": "image", "url": target}
        for key in ("width", "align", "alt"):
            if options.get(key):
                image[key] = str(options[key])
        body.append(image)
    elif kind == "interactive":
        # A whole page rather than a picture: an interactive Plotly or Altair
        # chart, a rendered notebook. Framed rather than inlined, so the
        # chart's own scripts and styles cannot reach the document around it —
        # a figure that restyles the page it is embedded in is a figure nobody
        # will use twice.
        body.append(
            {
                "type": "embed",
                "url": target,
                "width": str(options.get("width") or "100%"),
                "height": str(options.get("height") or EMBED_HEIGHT),
            }
        )
    elif kind == "table":
        body.extend(
            data_table(
                source,
                # Defaulted rather than looked up, so `kind="table"` works on
                # the `.dat` that is the reason anybody would write it.
                TABULAR.get(Path(target).suffix.lower(), ","),
                int(options.get("rows") or TABLE_ROW_LIMIT),
            )
        )
    else:
        # Neither a picture nor a table. Naming it and showing its provenance
        # is all that is left; an image here would give Quarto a file it cannot
        # display and a broken picture in the page.
        body.append(paragraph(code(target)))

    panel: list[dict[str, Any]] = []
    if as_bool(options.get("provenance")):
        panel.append(render(check_path(source), source, NOUN[kind]))
    return {"kind": kind, "body": body, "panel": panel}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # No arguments: report what this extension provides. Nothing in the render
    # path calls this — it is how a person checks that the executable the Lua
    # half will spawn is the one they think it is.
    if not argv:
        json.dump(SPEC, sys.stdout)
        return 0

    kind, name = (argv + ["", ""])[:2]
    payload = json.load(sys.stdin)

    if kind == "--directive" and name == "fromwhere":
        json.dump(run_directive(payload), sys.stdout)
        return 0

    # Anything else is a fromwhere/extension version mismatch rather than a
    # user error, so say so on stderr where `quarto render --log-level info`
    # shows it.
    print(
        f"fromwhere-quarto: unsupported request {kind} {name}", file=sys.stderr
    )
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
