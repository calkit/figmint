"""A Quarto extension that renders an artifact together with what is known
about it.

This is the MyST plugin's twin, and deliberately so. Quarto embeds a figure
perfectly well on its own; what it cannot do is answer the questions a reader
of a research document actually has — what was this made from, is any of it
machine-generated, and is it still consistent with the files behind it. The
answers live in `figmint.toml` and in the artifact's own Content Credentials,
and without something like this they stop at the command line where only the
author ever saw them.

A fenced div does the work of MyST's directive, because it is the one Quarto
construct that takes an argument, options, and a *parsed* caption:

    ::: {.figmint src="figures/composite.svg" #fig-performance width="95%"}
    Power coefficient against tip speed ratio.
    :::

    ::: {.figmint-provenance artifact="_site/index.html"}
    :::

The split between this module and `figmint.lua` is not arbitrary. Everything
that decides *what to say* — the freshness chain, the AI disclosure, the
headline — is Python, shared with the MyST plugin down to the node builders, so
the two documents cannot drift apart or from `figmint status`. The Lua half
only translates those nodes into Pandoc's AST and hands them to Quarto, which
is the one thing Python cannot do from outside the render.

The protocol matches `figmint-myst`: called with no arguments it prints its
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
    NOUN,
    TABLE_ROW_LIMIT,
    TABULAR,
    as_bool,
    code,
    data_table,
    display_kind,
    paragraph,
    render,
)
from .myst import (
    document_directive as myst_document_directive,
)
from .status import check_path

SPEC: dict[str, Any] = {
    "name": "figmint",
    "author": "figmint",
    "license": "MIT",
    "directives": [
        {
            "name": "figmint",
            "doc": (
                "Embed an artifact together with what it was made from and "
                "whether it is still current."
            ),
            "options": {
                "src": {
                    "type": "string",
                    "doc": (
                        "Path to the artifact, relative to the document. "
                        "Required."
                    ),
                    "required": True,
                },
                "width": {
                    "type": "string",
                    "doc": "Rendered width, as on any Quarto image.",
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
            },
        },
        {
            "name": "figmint-provenance",
            "doc": (
                "Summarize the whole document's provenance: every recorded "
                "artifact and whether it is current."
            ),
            "options": {
                "table": {
                    "type": "boolean",
                    "doc": "Show the artifact table.",
                },
                "graph": {
                    "type": "boolean",
                    "doc": "Draw the derivation DAG. Defaults to true.",
                },
                "direction": {
                    "type": "string",
                    "doc": "Mermaid direction: LR (default), TD, RL, BT.",
                },
                "artifact": {
                    "type": "string",
                    "doc": (
                        "This document's own output(s), comma separated. "
                        "Named so the panel can show how to rebuild them and "
                        "leave them out of its own freshness tally."
                    ),
                },
            },
        },
    ],
}


#: Where a Quarto project keeps its extensions. Quarto looks nowhere else.
EXTENSION_DIR = Path("_extensions") / "figmint"

#: The files that make up the extension, shipped inside the package.
EXTENSION_FILES = ("_extension.yml", "figmint.lua")

#: What a project has to add to `_quarto.yml` for the filter to run, and where
#: in the pipeline it has to run. Printed by the installer rather than left in
#: a README somebody has to find.
FILTER_CONFIG = """\
filters:
  - at: pre-ast
    path: figmint\
"""


def install_extension(destination: Path) -> Path:
    """Copy the Quarto extension into a project.

    `quarto add` would fetch the extension from a repository, at whatever
    version happens to be tagged there. The Lua filter and `figmint-quarto`
    speak a protocol private to figmint, so the pair has to move together —
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
    figmint, or a document in a subdirectory would look up an artifact that is
    not there. The MyST plugin gets this from mystmd's own working directory;
    here the Lua half sends it, and moving into it means every path in the
    shared rendering code means the same thing under both.
    """
    base = data.get("base")
    if base and Path(base).is_dir():
        os.chdir(base)


def run_directive(data: dict[str, Any]) -> dict[str, Any]:
    """One artifact: what to show for it, and what is known about it.

    The body and the panel are handed back separately because only the Lua half
    can finish the body — a Quarto figure carries the caption and the
    `#fig-...` label that make it numbered and cross-referenceable, and those
    arrive from the document rather than from here.
    """
    _relocate(data)
    options = data.get("options") or {}
    target = str(options.get("src") or "").strip()
    if not target:
        return {
            "kind": "artifact",
            "body": [paragraph(code("figmint: no src given"))],
            "panel": [],
        }

    source = Path(target)
    if not source.is_absolute():
        source = Path.cwd() / source

    kind = display_kind(target)
    body: list[dict[str, Any]] = []
    if kind == "figure":
        image: dict[str, Any] = {"type": "image", "url": target}
        for key in ("width", "align", "alt"):
            if options.get(key):
                image[key] = str(options[key])
        body.append(image)
    elif kind == "table":
        body.extend(
            data_table(
                source,
                TABULAR[Path(target).suffix.lower()],
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


def document_directive(data: dict[str, Any]) -> dict[str, Any]:
    """The document as a composite, one level up from a single artifact."""
    _relocate(data)
    return {
        "kind": "provenance",
        "body": [],
        "panel": myst_document_directive(data),
    }


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

    if kind == "--directive" and name == "figmint":
        json.dump(run_directive(payload), sys.stdout)
        return 0
    if kind == "--directive" and name == "figmint-provenance":
        json.dump(document_directive(payload), sys.stdout)
        return 0

    # Anything else is a figmint/extension version mismatch rather than a user
    # error, so say so on stderr where `quarto render --log-level info` shows
    # it.
    print(
        f"figmint-quarto: unsupported request {kind} {name}", file=sys.stderr
    )
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
