"""A MyST plugin that renders a figure together with what is known about it.

MyST embeds a figure perfectly well on its own. What it cannot do is answer the
questions a reader of a research document actually has: what was this made from,
is any of it machine-generated, and is it still consistent with the files behind
it. That information exists — in `figmint.toml` and in the artifact's own
Content Credentials — but nothing carried it into the rendered document, so it
stopped at the command line where only the author ever saw it.

`:::{figmint}` emits the same figure node MyST would have produced, so numbering
and `[](#fig-...)` cross-references behave normally, and attaches the provenance
underneath it. `:::{figmint-provenance}` does the same for the document as a
whole, which is a composite artifact in its own right.

It is an *executable* plugin rather than a JavaScript one, which mystmd supports
by spawning a program and speaking JSON to it: called with no arguments it
prints its specification, and called with `--directive <name>` it reads the
directive payload on stdin and writes AST nodes to stdout. That matters here
because it means the document is rendered by the same code that answers
`figmint status` — a JavaScript plugin would have had to reimplement the store
reader and the C2PA reader, and would have drifted from them.

Wire it up in `myst.yml`:

    project:
      plugins:
        - type: executable
          path: ./.venv/bin/figmint-myst
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .openserver import URL_ENV as OPEN_URL_ENV
from .status import (
    ArtifactStatus,
    State,
    check_all,
    check_artifact,
    check_path,
)
from .store import Store, hash_file, project_root

# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------

STATE_MARK: dict[State, str] = {
    State.OK: "✅",
    State.STALE: "⚠️",
    State.MISSING: "⛔",
    State.MODIFIED: "⚠️",
    State.UNTRACKED: "❔",
}

STATE_WORD: dict[State, str] = {
    State.OK: "unchanged",
    State.STALE: "changed since",
    State.MISSING: "missing",
    State.MODIFIED: "edited outside figmint",
    State.UNTRACKED: "not recorded",
}


# --------------------------------------------------------------------------
# AST helpers
#
# MyST's AST is plain JSON, so these are just dict constructors. Keeping them
# named makes the node-building below readable as document structure.
# --------------------------------------------------------------------------


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "value": value}


def strong(value: str) -> dict[str, Any]:
    return {"type": "strong", "children": [text(value)]}


def code(value: str) -> dict[str, Any]:
    return {"type": "inlineCode", "value": value}


def link(url: str, label: str) -> dict[str, Any]:
    return {"type": "link", "url": url, "children": [text(label)]}


def paragraph(*children: dict[str, Any]) -> dict[str, Any]:
    return {"type": "paragraph", "children": list(children)}


def cell(*children: dict[str, Any], header: bool = False) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "tableCell", "children": list(children)}
    if header:
        node["header"] = True
    return node


def row(*cells: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tableRow", "children": list(cells)}


def table(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"type": "table", "children": list(rows)}


def mermaid(value: str) -> dict[str, Any]:
    """MyST parses ```{mermaid} into this node and draws it with no help from us."""
    return {"type": "mermaid", "value": value}


def emphasis(value: str) -> dict[str, Any]:
    return {"type": "emphasis", "children": [text(value)]}


def admonition(
    kind: str, title: str, *children: dict[str, Any], dropdown: bool = False
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "admonition",
        "kind": kind,
        "children": [
            {"type": "admonitionTitle", "children": [text(title)]},
            *children,
        ],
    }
    if dropdown:
        node["class"] = "dropdown"
    return node


def caption_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the already-parsed caption out of the directive node.

    MyST hands over both the raw body string and its parsed form; using the
    parsed one means emphasis, citations, and cross-references in a caption keep
    working instead of arriving as literal text.
    """
    for child in node.get("children") or []:
        if child.get("type") == "mystDirectiveBody":
            return child.get("children") or []
    return []


def as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "no", "0", "off")


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def chain_block(report: ArtifactStatus, store: Store) -> dict[str, Any]:
    """The whole derivation behind this artifact, not just its direct inputs.

    A composite's immediate input is a `.drawio`, which tells a reader nothing.
    What they want is the chain that ends in data — so the graph is scoped to
    this artifact's ancestors and drawn the same way the document-level one is.
    """
    from .graph import build, to_mermaid

    try:
        graph = build(store.root)
        keep = _ancestors(report.path, store)
        graph.nodes = [n for n in graph.nodes if n.id in keep]
        graph.edges = [
            e for e in graph.edges if e.source in keep and e.target in keep
        ]
        if graph.empty:
            return paragraph(emphasis("No derivation to draw."))
        return mermaid(to_mermaid(graph))
    except Exception as exc:  # noqa: BLE001 - never fail a build over a diagram
        return paragraph(emphasis(f"Provenance graph failed: {exc}"))


def _ancestors(path: str, store: Store) -> set[str]:
    """This artifact and everything it was derived from, transitively."""
    seen: set[str] = set()
    queue = [path]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        artifact = store.artifacts.get(current)
        if artifact:
            queue.extend(i.path for i in artifact.inputs)
    return seen


def credentials_for(path: Path):
    """Content Credentials on the artifact itself, if it carries any."""
    from . import credentials as credentials_mod

    try:
        return credentials_mod.read(path)
    except Exception:  # noqa: BLE001 - a build must not die over a bad manifest
        return None


def provenance_cell(item, store: Store) -> dict[str, Any]:
    """What the record says about where one input came from.

    This is the column a reader actually needs. "file" told them nothing they
    could not see from the path; whether the file was derived, declared, or
    simply appeared is the question the panel exists to answer, and answering it
    per-input is what makes a composite figure's provenance legible rather than
    merely present.
    """
    if item.kind == "environment":
        return cell(text("environment lock"))

    artifact = store.artifacts.get(item.path)
    if artifact is None:
        # Nothing in the record accounts for it. Said plainly, because every
        # other check in this panel passes and this is the one that does not.
        return cell(emphasis("⚠ undeclared"))
    if artifact.command:
        return cell(text("derived by figmint"))
    if artifact.kind == "drawio-import":
        # Recorded, with inputs, but no command: a diagram is arranged by hand
        # afterwards, so naming a command would promise a reproduction that does
        # not exist.
        return cell(text("assembled in draw.io"))

    origin = artifact.origin_description()
    if not origin:
        return cell(emphasis("⚠ no origin recorded"))
    if artifact.origin_kind == "doi":
        return cell(link(f"https://doi.org/{artifact.origin}", origin))
    if artifact.origin_ai:
        # Machine-generated material is the one origin a reader should not have
        # to squint at, so it is emphasised rather than set flush with the rest.
        # The accountable person is named in the same string, never dropped.
        return cell(emphasis(origin))
    return cell(text(origin))


@dataclass
class ChainItem:
    """One file somewhere in an artifact's chain."""

    path: str
    kind: str
    state: State
    #: True when the artifact names it directly, rather than inheriting it from
    #: something further back.
    direct: bool


def chain_items(report: ArtifactStatus, store: Store) -> list[ChainItem]:
    """Every file behind this artifact, not just the ones it names.

    A composite's only direct input is a `.drawio`, which tells a reader
    nothing: the panels, the data they were plotted from, the script that drew
    them and the environment it ran in are all one level further back. Listing
    the transitive chain is what makes the table say the same thing as the
    diagram beside it.
    """
    direct = {i.path for i in report.inputs}
    paths = _ancestors(report.path, store) - {report.path}

    # The first record naming a file also fixes the hash it is checked against,
    # which is what lets a file no command produced be checked at all.
    named: dict[str, Any] = {}
    for artifact in store.artifacts.values():
        for item in artifact.inputs:
            named.setdefault(item.path, item)

    # Direct inputs first, then everything behind them: "what this was made
    # from" before "and what that came from" is the order a reader asks in.
    items: list[ChainItem] = []
    for path in sorted(paths, key=lambda p: (p not in direct, p)):
        item = named.get(path)
        artifact = store.artifacts.get(path)
        if artifact is not None:
            state = check_artifact(store, artifact).state
        elif item is None:
            state = State.UNTRACKED
        else:
            target = store.root / path
            if not target.is_file():
                state = State.MISSING
            elif hash_file(target) != item.hash:
                state = State.STALE
            else:
                state = State.OK
        items.append(
            ChainItem(
                path=path,
                kind=item.kind if item else "file",
                state=state,
                direct=path in direct,
            )
        )
    return items


def input_row(item, store: Store) -> dict[str, Any]:
    return row(
        cell(code(item.path)),
        provenance_cell(item, store),
        cell(text(f"{STATE_MARK[item.state]} {STATE_WORD[item.state]}")),
    )


def inputs_table(items: list[ChainItem], store: Store) -> dict[str, Any]:
    return table(
        row(
            cell(strong("Input"), header=True),
            cell(strong("Provenance"), header=True),
            cell(strong("State"), header=True),
        ),
        *(input_row(i, store) for i in items),
    )


def origin_paragraph(report: ArtifactStatus) -> dict[str, Any] | None:
    """The artifact's own origin, when nothing produced it.

    A declared primary has no command and no inputs, so without this the panel
    would have nothing to say about the very files the declaration exists for.
    """
    artifact = report.artifact
    if artifact is None or artifact.command or not artifact.origin_kind:
        return None

    described = artifact.origin_description()
    if artifact.origin_kind == "doi":
        body: dict[str, Any] = link(
            f"https://doi.org/{artifact.origin}", described
        )
    else:
        body = text(described)

    children = [strong("Origin: "), body]
    if artifact.origin_kind == "attested":
        children.append(emphasis(" — a declaration; nothing can verify it"))
    return paragraph(*children)


def headline(
    report: ArtifactStatus, creds, chain: list[ChainItem]
) -> tuple[str, str]:
    """Admonition kind and title summarising the artifact's standing."""
    if report.state is State.UNTRACKED:
        return "warning", "Not tracked — nothing recorded for this artifact"
    if report.state is State.MISSING:
        return "danger", "The artifact no longer exists"
    if report.state is State.MODIFIED:
        return (
            "danger",
            "Edited outside figmint — the record no longer describes this file",
        )
    if report.state is State.STALE:
        changed = ", ".join(i.path for i in report.changed_inputs)
        return "danger", f"Out of date — {changed} changed since this was made"

    # Every direct link checks out, but staleness does not stop at the first
    # one. A composite whose `.drawio` is untouched is reported OK by
    # `check_artifact` even when the data three steps back has moved, because
    # nothing regenerated the panel in between. Saying "up to date" there would
    # be the most misleading thing this panel could print.
    broken = [i for i in chain if i.state is not State.OK]
    if broken:
        names = ", ".join(i.path for i in broken)
        return (
            "danger",
            f"Out of date upstream — {names} changed, but nothing between "
            f"that and this figure has been regenerated",
        )

    disclosure = ""
    if creds and creds.machineGenerated:
        disclosure = ", contains machine-generated material"
    return "note", f"Up to date — {len(chain)} recorded input(s){disclosure}"


def credentials_paragraph(creds) -> dict[str, Any] | None:
    """What the artifact's own manifest says, when it has one.

    Kept separate from the inputs table because it answers a different
    question: the table says *what this was made from*, the manifest says *who
    signed for it and whether the signature holds*.
    """
    if creds is None:
        return None

    parts: list[dict[str, Any]] = [strong("Content Credentials: ")]
    state = creds.validationState or "unknown"
    parts.append(text(state.lower()))
    if creds.signedBy:
        parts.append(text(f", signed by {creds.signedBy}"))
    if creds.warnings and "signingCredential.untrusted" in creds.warnings:
        # Valid and untrusted are different things, and collapsing them is how
        # a self-signed artifact ends up looking endorsed.
        parts.append(text(" (signer not in a trust list)"))
    if creds.machineGenerated:
        parts.append(text(". "))
        parts.append(strong("Machine-generated"))
        if creds.machineGeneratedBy:
            parts.append(text(f" — declared by {creds.machineGeneratedBy}"))
    parts.append(text("."))
    return paragraph(*parts)


def editable_source(report: ArtifactStatus, store: Store) -> str | None:
    """The `.drawio` behind this artifact, if there is one.

    A published SVG is a build artifact; editing it works right up until the
    next export overwrites the edit. The diagram one step back is the file a
    reader who wants to *change* the figure actually needs, so that is what the
    link points at.
    """
    seen: set[str] = set()
    queue = [report.path]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        if current.endswith(".drawio"):
            return current
        artifact = store.artifacts.get(current)
        if artifact:
            queue.extend(i.path for i in artifact.inputs)
    return None


def edit_paragraph(diagram: str, store: Store) -> dict[str, Any]:
    """A way to open the diagram, or the path to open by hand.

    An http link is the only form that works where this is most often read.
    VS Code's Simple Browser is a webview and will not follow a `vscode://`
    URL — it navigates to it and shows a blank page — so the link points at a
    loopback endpoint that answers 204 and opens the file locally.

    The link only appears when that endpoint is running. A published build gets
    the path as text instead, rather than a dead link to a port on the author's
    laptop.
    """
    opener = os.environ.get(OPEN_URL_ENV)
    if opener:
        return paragraph(
            link(f"{opener}?path={quote(diagram)}", "✎ Edit in draw.io"),
            text(" — opens "),
            code(diagram),
        )
    return paragraph(strong("Edit: "), code(diagram))


def render(report: ArtifactStatus, source: Path) -> dict[str, Any]:
    creds = credentials_for(source) if source.is_file() else None
    store = Store.for_path(source)
    chain = chain_items(report, store) if report.artifact else []
    kind, title = headline(report, creds, chain)

    children: list[dict[str, Any]] = []
    if chain:
        children.append(inputs_table(chain, store))
    origin = origin_paragraph(report)
    if origin:
        children.append(origin)
    if report.artifact and report.artifact.command:
        children.append(
            paragraph(strong("Produced by: "), code(report.artifact.command))
        )
    manifest = credentials_paragraph(creds)
    if manifest:
        children.append(manifest)

    if chain:
        children.append(chain_block(report, store))
    diagram = editable_source(report, store)
    if diagram:
        children.append(edit_paragraph(diagram, store))
    # Keyed off the headline rather than `report.stale`, so an artifact that is
    # sound in itself but rests on a broken chain gets the same instruction.
    if kind == "danger":
        children.append(
            paragraph(
                text("Regenerate it; "),
                emphasis("do not edit figmint.toml to make this pass"),
                text("."),
            )
        )

    # Collapsed when there is nothing wrong, so a long document does not turn
    # into a wall of metadata.
    return admonition(kind, title, *children, dropdown=(kind == "note"))


# --------------------------------------------------------------------------
# The directives
# --------------------------------------------------------------------------

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
            "arg": {
                "type": "string",
                "doc": "Path to the artifact, relative to the MyST project.",
                "required": True,
            },
            "options": {
                "name": {
                    "type": "string",
                    "doc": "Label for cross-referencing.",
                },
                "width": {
                    "type": "string",
                    "doc": "Rendered width, as on `figure`.",
                },
                "align": {"type": "string", "doc": "left, center, or right."},
                "alt": {"type": "string", "doc": "Alt text."},
                "provenance": {
                    "type": "boolean",
                    "doc": "Show the provenance panel. Defaults to true.",
                },
            },
            "body": {"type": "parsed", "doc": "The caption."},
        },
        {
            "name": "figmint-provenance",
            "doc": (
                "Summarise the whole document's provenance: every recorded "
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
            },
            "body": {"type": "parsed", "doc": "Optional lead-in text."},
        },
    ],
}

#: What MyST can put in an `image` node. A tracked artifact need not be one — a
#: dataset has provenance worth showing and nothing to display.
_RENDERABLE = (".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf")


def run_directive(data: dict[str, Any]) -> list[dict[str, Any]]:
    options = data.get("options") or {}
    node = data.get("node") or {}
    target = (data.get("arg") or "").strip()

    source = Path(target)
    if not source.is_absolute():
        source = Path.cwd() / source

    out: list[dict[str, Any]] = []
    caption = caption_children(node)

    if target.lower().endswith(_RENDERABLE):
        image: dict[str, Any] = {"type": "image", "url": target}
        for key in ("width", "align", "alt"):
            if options.get(key):
                image[key] = options[key]

        figure: dict[str, Any] = {
            "type": "container",
            "kind": "figure",
            "children": [image],
        }
        if options.get("name"):
            figure["identifier"] = str(options["name"]).lower()
            figure["label"] = str(options["name"])
        if caption:
            figure["children"].append({"type": "caption", "children": caption})
        out.append(figure)
    else:
        # Not an image. Naming it and showing its provenance is the point;
        # emitting an `image` node for a CSV makes MyST warn about an
        # unsupported extension and renders a broken picture.
        lead: list[dict[str, Any]] = [code(target)]
        if caption:
            lead.append(text(" — "))
            for child in caption:
                lead.extend(child.get("children") or [child])
        out.append(paragraph(*lead))

    if as_bool(options.get("provenance")):
        out.append(render(check_path(source), source))
    return out


def document_directive(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The document as a composite, one level up from a single artifact."""
    options = data.get("options") or {}
    root = project_root(Path.cwd())
    reports = check_all(root)

    if not reports:
        return [
            admonition(
                "warning",
                "Nothing recorded — no artifacts have been produced with `figmint run`",
            )
        ]

    children: list[dict[str, Any]] = []
    if as_bool(options.get("table")):
        children.append(document_table(reports))

    if as_bool(options.get("graph")):
        children.append(
            graph_block(root, reports, str(options.get("direction") or "LR"))
        )

    stale = [r for r in reports if r.stale]
    if stale:
        kind = "danger"
        title = f"{len(stale)} of {len(reports)} artifact(s) out of date"
    else:
        kind = "note"
        title = f"{len(reports)} artifact(s), all up to date"

    return [admonition(kind, title, *children, dropdown=(kind == "note"))]


def graph_block(
    root: Path, reports: list[ArtifactStatus], direction: str
) -> dict[str, Any]:
    """The derivation DAG, or a line saying why there is not one.

    Built from `figmint.toml`, which is already a DAG, so the diagram cannot
    disagree with the freshness reported beside it.
    """
    from .graph import build, to_mermaid

    try:
        graph = build(root, reports)
        if graph.empty:
            return paragraph(
                emphasis("No derivation to draw — nothing has inputs.")
            )
        return mermaid(to_mermaid(graph, direction=direction))
    except Exception as exc:  # noqa: BLE001 - never fail a build over a diagram
        return paragraph(emphasis(f"Provenance graph failed: {exc}"))


def document_table(reports: list[ArtifactStatus]) -> dict[str, Any]:
    rows = [
        row(
            cell(strong("Artifact"), header=True),
            cell(strong("Inputs"), header=True),
            cell(strong("State"), header=True),
        )
    ]
    for report in reports:
        rows.append(
            row(
                cell(code(report.path)),
                cell(text(str(len(report.inputs)))),
                cell(
                    text(
                        f"{STATE_MARK[report.state]} {STATE_WORD[report.state]}"
                    )
                    if report.state is not State.OK
                    else text("✅ up to date")
                ),
            )
        )
    return table(*rows)


# --------------------------------------------------------------------------
# The executable protocol
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # No arguments: report what this plugin provides.
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

    # Anything else is a mystmd/figmint version mismatch rather than a user
    # error, so say so on stderr where `myst build --debug` will show it.
    print(f"figmint-myst: unsupported request {kind} {name}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
