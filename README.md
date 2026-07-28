# Figmint

Create fresh figures.

Figmint provides a way to compose and edit figures for research articles in a
reproducible way. You pull panels in from a local directory, arrange them, add
labels and LaTeX and annotations, and the resulting figure keeps a record of
where every piece came from — and tells you when a piece has changed underneath
you.

## Quickstart

```sh
make install          # uv sync + npm install
make dev              # API on :8420, editor on http://localhost:5273
```

To try it against the bundled example:

```sh
make dev ROOT=examples FIGURES=figures
```

Then open <http://localhost:5273> and use **Open…** with
`two-panel.fig.yaml`.

Point it at real work with `ROOT` and `FIGURES`:

```sh
make dev ROOT=~/research/turbine-paper FIGURES=figures
```

`make help` lists the rest (`api`, `web`, `build`, `serve`, `test`, `check`).

## How it fits together

```
web/                 React + TypeScript + Vite editor
  src/model/         document model, geometry
  src/io/            .fig.yaml serialization, Stencila export, API client
  src/state/         editor store with undo/redo
  src/components/    canvas, panels, inspector
src/figmint/         Python backend
  assets.py          directory scan, content hashing, intrinsic sizes
  server.py          HTTP API; also serves the built editor
```

The backend owns everything that touches the filesystem, so provenance answers
come from the actual bytes on disk rather than from something the browser was
told. In development Vite proxies `/api` to it; in production
`figmint serve` serves the built editor itself.

The canvas is plain SVG in document coordinates — the same elements the exporter
writes. What you see and what you publish stay in agreement, and there is no
second rendering path to keep in sync.

## Documents

Figures are saved as `*.fig.yaml`: one human-readable, agent-editable file
holding the canvas, the provenance ledger, and every element's position.
See [docs/format.md](docs/format.md) for the full format and its mapping onto
[Stencila](https://stencila.io), and [docs/editor-design.md](docs/editor-design.md)
for the design intent.

Export currently produces Stencila Markdown (`.smd`) alongside the native YAML.
Panels become a `::: figure` with an inferred grid layout, annotations become a
`Figure.overlay` SVG using Stencila's `s:` component namespace, and exact
geometry round-trips through the frontmatter.

## Keyboard

| | |
| --- | --- |
| drag | move selection |
| shift-click | add to selection |
| drag on empty canvas | marquee select |
| space-drag, middle-drag | pan |
| ⌘/ctrl-scroll | zoom |
| alt | bypass grid snap while dragging |
| shift while resizing | preserve aspect ratio |
| arrows / shift-arrows | nudge 1pt / 10pt |
| ⌘Z, ⇧⌘Z | undo, redo |
| ⌘D | duplicate |
| ⌘\[, ⌘\] | send backward, bring forward |

## Development

```sh
make test     # vitest
make check    # typecheck + lint + test
```
