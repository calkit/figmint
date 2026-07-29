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
  src/model/         document model, geometry, layout solver
  src/io/            .fig.yaml serialization, Stencila export, API client
  src/state/         editor store with undo/redo
  src/components/    canvas, panels, inspector
src/figmint/         Python backend and CLI
  assets.py          directory scan, content hashing, intrinsic sizes
  credentials.py     reading C2PA Content Credentials
  provenance.py      provenance levels and project policy
  calkit.py          reading Calkit's pipeline to verify stage outputs
  importer.py        declaring the origin of imported components
  watch.py           filesystem watcher behind the live-update websocket
  document.py        .fig.yaml loading
  status.py          staleness checking
  build.py           composing to self-contained SVG/PDF/PNG
  server.py          HTTP API; also serves the built editor
  cli.py             serve / status / build / accept / check / import
```

The backend owns everything that touches the filesystem, so provenance answers
come from the actual bytes on disk rather than from something the browser was
told. In development Vite proxies `/api` to it; in production
`figmint serve` serves the built editor itself.

The canvas is plain SVG in document coordinates — the same elements the exporter
writes. What you see and what you publish stay in agreement, and there is no
second rendering path to keep in sync.

## Command line

The editor is not the only way in — `status` and `build` do the same work
headlessly, so an agent or a CI job can use them.

```sh
figmint check                  # is every component identified well enough?
figmint import photo.png --from 'Fig 3b, Smith et al. 2024' --doi 10.1000/x
figmint status                 # is every figure still true to its components?
figmint status -v figures/     # also list the healthy ones
figmint accept fig.fig.yaml    # "I've reviewed the change" — re-records hashes
figmint build fig.fig.yaml     # compose into a self-contained SVG
figmint build . --if-stale --to pdf --to svg
```

`status` exits `1` when anything is stale and `2` on error, so it drops straight
into a pipeline.

### Two kinds of stale

These are different questions, and figmint keeps them apart deliberately:

| | Question | Fixed by |
| --- | --- | --- |
| **Output** | Is the built figure older than its inputs? | `build` — purely mechanical |
| **Component** | Has a panel changed since it was placed? | `accept` — a judgement call |

Rebuilding does **not** clear a changed-component warning. The axes may have
moved, the units may have changed, the point the panel was making may no longer
hold. If `build` silently re-recorded hashes, every rebuild would erase the
evidence that anything had changed, and the warning would be worth nothing.

So the loop after a script regenerates a component is:

```sh
figmint status              # cp-curve changed on disk
# ...look at it...
figmint accept .            # yes, that's the figure I meant
figmint build . --if-stale  # regenerate the composite
```

`accept` rewrites the document with a round-trip YAML parser, so comments, key
order, and formatting survive — the diff is the hash line plus what it recorded.

A build inlines every vector panel rather than rasterising it, so text in a plot
stays selectable text all the way into the PDF, and the page comes out at true
size — a 468pt canvas is exactly 6.5in. PDF and PNG need `rsvg-convert` or
`inkscape` on the system; SVG needs nothing.

## Provenance

Where a component carries signed [C2PA Content Credentials](https://c2pa.org),
figmint reads them: who made it, with what tool, and whether any of it was
AI-generated. That is a cryptographically bound claim, unlike anything figmint
could infer from a filename.

### Nothing anonymous gets into a figure

A composite figure is only as trustworthy as its least accountable panel, so
figmint sorts components by how well their origin is known:

| Level | Meaning |
| --- | --- |
| `unidentified` | a file that simply appeared |
| `declared` | imported, with a stated origin (URL, DOI, citation) |
| `generated` | a sidecar names a producing script — nothing verifies it |
| `reproducible` | a Calkit stage declares it as an output — verifiable |
| `signed` | valid C2PA credentials naming the producer |

Set the minimum your project accepts in `figmint.toml`:

```toml
[provenance]
require = "declared"   # unidentified | declared | generated | reproducible | signed
enforce = true         # false to warn instead of block
```

The editor refuses to place a component below the bar, and `figmint check`
fails in CI. This is not meant to stop you using a micrograph or a figure from
a paper — `figmint import` records where it came from, and then it's fine.

The step from `generated` to `reproducible` is the one that matters: a sidecar
claiming `plot.py` made a file asserts something nothing checks, whereas a
Calkit stage declaring that exact path as an output can be verified without
running anything. See [the boundary note](docs/editor-design.md) for why figmint
references Calkit stages rather than defining its own.

To see it, generate a signed component with Stencila:

```sh
stencila credentials init     # once — creates a local signing identity
make components               # renders examples/components/schematic.smd
```

Components without credentials fall back to a `<artifact>.prov.yaml` sidecar,
and every component is content-hashed either way so the editor can tell you when
a panel has gone stale.

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

Select two or more panels and press **Grid** to arrange them; the group's
columns, gaps, and column weights are editable in the inspector, and export
straight to Stencila's `Figure.layout`.

## Development

```sh
make test     # vitest
make check    # typecheck + lint + test
```
