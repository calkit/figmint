"""A MyST plugin that renders a figure together with what is known about it.

MyST already embeds `figures/composite.drawio.svg` perfectly well. What it
cannot do is answer the questions a reader of a research document actually has:
where did each component come from, is any of it machine-generated, and is the
picture still consistent with the files it was built from. That information
exists — it is in the diagram and in `calkit.yaml` — but nothing carries it into
the rendered document, so it stops at the command line.

This plugin closes that gap. `:::{figmint}` emits the same figure node MyST would
have produced (so numbering and `[](#fig-...)` cross-references behave normally)
and attaches the provenance underneath it.

It is an *executable* plugin rather than a JavaScript one, which mystmd supports
by spawning a program and speaking JSON to it: called with no arguments it prints
its specification, and called with `--directive <name>` it reads the directive
payload on stdin and writes AST nodes to stdout. That matters here because it
means the document is rendered by the same code that answers `figmint check` —
a JavaScript plugin would have had to reimplement the draw.io reader, the C2PA
reader, and the policy ladder, and would have drifted from them.

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
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .openserver import URL_ENV as OPEN_URL_ENV
from .provenance import Level
from .report import ComponentReport, FigureReport, inspect_document
from .status import SourceState

# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------

#: Emoji rather than colour alone, because the book theme renders admonition
#: colour but a table cell has no styling hook an executable plugin can reach.
LEVEL_MARK: dict[Level, str] = {
    Level.UNIDENTIFIED: "⛔",
    Level.DECLARED: "📝",
    Level.GENERATED: "⚙️",
    Level.REPRODUCIBLE: "🔁",
    Level.SIGNED: "🔏",
}

STATE_MARK: dict[SourceState, str] = {
    SourceState.OK: "✅",
    SourceState.STALE: "⚠️",
    SourceState.MISSING: "⛔",
    SourceState.UNKNOWN: "❔",
}

#: What the "In figure" column compares: the copy embedded in the diagram
#: against the file on disk. Deliberately *not* the word "current" — a copy can
#: match its source exactly while that source is itself out of date, and calling
#: that "current" next to a red staleness banner reads as a contradiction.
STATE_WORD: dict[SourceState, str] = {
    SourceState.OK: "matches source",
    SourceState.STALE: "differs from source",
    SourceState.MISSING: "source is gone",
    SourceState.UNKNOWN: "cannot tell",
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


# --------------------------------------------------------------------------
# Rendering the report
# --------------------------------------------------------------------------


def source_word(component: ComponentReport) -> str:
    """The "Source" column: the file on disk, against whatever produces it.

    Separate from the "In figure" column because they are separate questions and
    can disagree — which is exactly the case worth showing. A component embedded
    faithfully from a file that itself needs regenerating is up to date in one
    sense and out of date in the one that matters.
    """
    if component.upstream_stale:
        return "⚠️ needs regenerating"
    if component.stage and component.stage_current:
        return "✅ up to date"
    if component.stage:
        # A stage exists but there is no lock to check it against.
        return "❔ never run here"
    return "— not generated here"


def embedded_word(component: ComponentReport) -> str:
    """The "In figure" column: the embedded copy against the file on disk.

    A faithful copy of a file that itself needs regenerating does not earn a
    green tick. The comparison genuinely passed, but a ✅ in a row whose
    provenance is broken reads as "this component is fine" — the opposite of
    what the rest of the row says. The mark tracks whether the component can be
    trusted, and the words stay precise about what was actually compared.
    """
    if component.state is SourceState.OK and component.upstream_stale:
        return "⚠️ matches, but the source is stale"
    return f"{STATE_MARK[component.state]} {STATE_WORD[component.state]}"


def component_row(component: ComponentReport) -> dict[str, Any]:
    """One line of the provenance table."""
    origin: dict[str, Any] = (
        code(component.origin) if component.origin else text(component.label)
    )

    level = f"{LEVEL_MARK[component.level]} {component.level.slug}"
    embedded = embedded_word(component)

    # The reason is the interesting column: "Calkit stage `plot-cp`" and "C2PA
    # credentials verify" are what make the level something other than a badge.
    detail: list[dict[str, Any]] = [text(component.reason)]
    if component.credentials and component.credentials.get("machineGenerated"):
        detail.append(text(" "))
        detail.append(strong("AI-generated"))
    if component.detail:
        detail.append(text(f" — {component.detail}"))

    return row(
        cell(origin),
        cell(text(level)),
        cell(text(source_word(component))),
        cell(text(embedded)),
        cell(*detail),
    )


def provenance_table(report: FigureReport) -> dict[str, Any]:
    return table(
        row(
            cell(strong("Component"), header=True),
            cell(strong("Provenance"), header=True),
            # Two freshness columns, because there are two ways to be out of
            # date and one can be fine while the other is not.
            cell(strong("Source"), header=True),
            cell(strong("In figure"), header=True),
            cell(strong("Basis"), header=True),
        ),
        *(component_row(c) for c in report.components),
    )


def headline(report: FigureReport) -> tuple[str, str]:
    """Admonition kind and title summarising the figure's standing."""
    if report.error:
        return "danger", f"figmint could not read this figure: {report.error}"
    if not report.components:
        return "warning", "No tracked components in this figure"

    # Checked before the components, because when the figure itself is behind
    # its source the picture on the page is wrong, and that outranks anything
    # about what is embedded inside it.
    if report.behind_source:
        changed = ", ".join(report.document_stage_changed) or "its source"
        return (
            "danger",
            f"This figure is out of date — {changed} changed since it was "
            f"rendered, so the picture above is not what the source says",
        )

    stale = [c for c in report.components if c.stale]
    if stale:
        names = ", ".join(c.label for c in stale)
        return (
            "danger",
            f"Out of date — {len(stale)} component(s) changed since this was "
            f"built: {names}",
        )

    # A component can match its file exactly and still be out of date, because
    # file itself is the output of a stage that has not been re-run. Nothing
    # about the figure looks wrong in that case, which is why it needs saying.
    upstream = report.upstream_stale
    if upstream:
        names = ", ".join(c.label for c in upstream)
        changed = sorted({dep for c in upstream for dep in c.stage_changed})
        because = f" ({', '.join(changed)} changed)" if changed else ""
        return (
            "danger",
            f"Upstream out of date — {names} needs regenerating{because}",
        )

    violations = report.violations
    if violations:
        return (
            "warning",
            f"{len(violations)} component(s) below the project's "
            f"`{report.policy.require.slug}` bar",
        )

    # Every component checks out, and the figure still rests on a file nobody
    # can account for. This is the failure the other checks are structurally
    # unable to see: they all look at the components, and the gap is one link
    # further back.
    roots = report.unaccounted_inputs
    if roots:
        return (
            "warning",
            f"Input data with no stated origin: {', '.join(roots)}",
        )

    n = len(report.components)
    return "note", f"{n} component(s), everything up to date"


def editable_source(target: Path) -> Path:
    """The file a human should actually edit.

    A `.drawio.svg` produced by the pipeline is a build artifact: editing it
    works, right up until the next run overwrites the edit. When an authored
    `.drawio` sits beside it, that is the file the edit button should open.
    """
    name = target.name
    if name.endswith(".drawio.svg"):
        authored = target.with_name(name[: -len(".svg")])
        if authored.is_file():
            return authored
    return target


def shown_path(target: Path) -> str:
    """The source path, as someone would type it."""
    try:
        return target.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return str(target)


def action_paragraph(
    report: FigureReport, source: Path, editor: bool
) -> dict[str, Any] | None:
    """What to do about it: open the editor, or the command that fixes it."""
    actions: list[dict[str, Any]] = []

    if editor and source.exists():
        target = editable_source(source)
        opener = os.environ.get(OPEN_URL_ENV)
        if opener:
            # `figmint watch --open-server` is running, so an http link can do
            # the job. This is the only form that works inside VS Code's Simple
            # Browser, which is a webview and will not follow `vscode://` — it
            # navigates to the URL and shows a blank page. The endpoint answers
            # 204, so clicking it opens the editor without moving the reader
            # off the document.
            actions.append(
                link(f"{opener}?path={quote(shown_path(target))}", "✎ Open in draw.io")
            )
        else:
            # Nothing to click, so name the file instead.
            #
            # Deliberately not a markdown link to the source: MyST resolves a
            # relative link to a project file by copying it into the build under
            # a content-hashed name, so the link would hand the reader a
            # duplicate. Editing that duplicate is lost work — a worse outcome
            # than having no link at all. A path someone can open themselves
            # makes no promise it cannot keep, and unlike a command it does not
            # assume this project has a Makefile.
            actions.append(text("Source: "))
            actions.append(code(shown_path(target)))

    if report.behind_source:
        if actions:
            actions.append(text(" · "))
        actions.append(text("re-render with "))
        actions.append(code("calkit run"))
    elif report.upstream_stale:
        # Re-embedding would faithfully copy an out-of-date file, so the
        # pipeline has to run first. Suggesting `make refresh` here would send
        # someone in a circle.
        if actions:
            actions.append(text(" · "))
        actions.append(text("regenerate with "))
        actions.append(code("calkit run"))
    elif report.stale:
        if actions:
            actions.append(text(" · "))
        actions.append(text("refresh with "))
        actions.append(code("make refresh"))

    for component in report.violations:
        if component.fix:
            if actions:
                actions.append(text(" · "))
            actions.append(text(f"{component.label}: "))
            actions.append(code(component.fix))

    return paragraph(*actions) if actions else None


def graph_block(
    report: FigureReport, source: Path, preset: str, detail: str
) -> dict[str, Any]:
    """The provenance graph, or a line saying why there is not one.

    Only reached when the author asked for it, so a reason is more useful than
    silence — a missing optional dependency should not look like a figure with
    no provenance.
    """
    from .graph import GraphUnavailable, figure_mermaid

    root = source.parent
    from . import calkit as calkit_mod

    project_root = calkit_mod.find_project(root) or Path.cwd()
    try:
        return mermaid(
            figure_mermaid(report, project_root, preset=preset, detail=detail)
        )
    except GraphUnavailable as exc:
        return paragraph(emphasis(f"Provenance graph unavailable: {exc}"))
    except Exception as exc:  # noqa: BLE001 - never fail a document build over a diagram
        return paragraph(emphasis(f"Provenance graph failed: {exc}"))


def render(
    report: FigureReport,
    source: Path,
    *,
    editor: bool,
    graph_preset: str | None = None,
    graph_detail: str = "medium",
) -> dict[str, Any]:
    kind, title = headline(report)
    children: list[dict[str, Any]] = []
    if report.components:
        children.append(provenance_table(report))

    if graph_preset:
        children.append(graph_block(report, source, graph_preset, graph_detail))

    # Said in the body as well as the headline, because when something else is
    # wrong the headline is spent on that and this would otherwise vanish.
    roots = report.unaccounted_inputs
    if roots:
        detail: list[dict[str, Any]] = [
            strong("Unaccounted input data. "),
            text("This figure is derived from "),
        ]
        for index, path in enumerate(roots):
            if index:
                detail.append(text(", "))
            detail.append(code(path))
        detail.append(
            text(
                ", which no pipeline stage produces and no `imported_from` in "
                "calkit.yaml explains. Everything above it is reproducible; the "
                "chain simply stops here."
            )
        )
        children.append(paragraph(*detail))
    actions = action_paragraph(report, source, editor)
    if actions:
        children.append(actions)
    # Collapsed when there is nothing wrong: the provenance should be available
    # without turning every figure in the document into a wall of metadata.
    return admonition(kind, title, *children, dropdown=(kind == "note"))


# --------------------------------------------------------------------------
# The directive
# --------------------------------------------------------------------------

SPEC: dict[str, Any] = {
    "name": "figmint",
    "author": "figmint",
    "license": "MIT",
    "directives": [
        {
            "name": "figmint",
            "doc": (
                "Embed a figmint figure together with the provenance of its "
                "components and a warning when any of them has changed."
            ),
            "arg": {
                "type": "string",
                "doc": "Path to the figure, relative to the MyST project.",
                "required": True,
            },
            "options": {
                "name": {
                    "type": "string",
                    "doc": "Label for cross-referencing, as on `figure`.",
                },
                "width": {"type": "string", "doc": "Rendered width, as on `figure`."},
                "align": {"type": "string", "doc": "left, center, or right."},
                "alt": {"type": "string", "doc": "Alt text."},
                "provenance": {
                    "type": "boolean",
                    "doc": "Show the provenance panel. Defaults to true.",
                },
                "editor": {
                    "type": "boolean",
                    "doc": "Show an 'open in draw.io' link. Defaults to true.",
                },
                "graph": {
                    "type": "string",
                    "doc": (
                        "Render the provenance graph as Mermaid. `true` picks a "
                        "view automatically; or name one: data-flow, "
                        "software-dependencies, citations, reactivity, full. "
                        "Requires the Stencila SDK."
                    ),
                },
                "graph-detail": {
                    "type": "string",
                    "doc": "low, medium (default), or high.",
                },
            },
            "body": {"type": "parsed", "doc": "The caption."},
        }
    ],
}


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


#: Presets the projection understands, plus `auto`.
GRAPH_PRESETS = (
    "auto",
    "full",
    "data-flow",
    "software-dependencies",
    "citations",
    "reactivity",
)


def graph_option(value: Any) -> str | None:
    """`:graph:` takes a boolean or a preset name; None means do not render."""
    if value is None or value is False:
        return None
    if value is True:
        return "auto"
    text_value = str(value).strip().lower()
    if text_value in ("false", "no", "0", "off", ""):
        return None
    if text_value in ("true", "yes", "1", "on"):
        return "auto"
    return text_value if text_value in GRAPH_PRESETS else "auto"


def as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "no", "0", "off")


def run_directive(data: dict[str, Any]) -> list[dict[str, Any]]:
    options = data.get("options") or {}
    node = data.get("node") or {}
    target = (data.get("arg") or "").strip()

    source = Path(target)
    if not source.is_absolute():
        source = Path.cwd() / source

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

    caption = caption_children(node)
    if caption:
        figure["children"].append({"type": "caption", "children": caption})

    out: list[dict[str, Any]] = [figure]

    if as_bool(options.get("provenance")):
        report = inspect_document(source)
        out.append(
            render(
                report,
                source,
                editor=as_bool(options.get("editor")),
                graph_preset=graph_option(options.get("graph")),
                graph_detail=str(options.get("graph-detail") or "medium"),
            )
        )

    return out


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

    # Anything else is a mystmd/figmint version mismatch rather than a user
    # error, so say so on stderr where `myst build --debug` will show it.
    print(f"figmint-myst: unsupported request {kind} {name}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
