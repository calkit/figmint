# figmint + MyST example

A playground for the editing loop: a MyST document whose figure is a draw.io
diagram, with figmint tracking where each panel came from and whether any of
them has gone stale — and showing all of that *in the rendered document*, not
just at the command line.

```sh
uv sync          # figmint (editable, from ../..), matplotlib, mystmd, jupyter
make preview     # live document + component watcher
```

One prerequisite lives outside the environment: **draw.io**, used to render the
diagram and to open it for editing. It is a desktop app, so it cannot be a
project dependency — `brew install --cask drawio`, or run that stage in a
container if you need the pipeline fully hermetic. Everything else, including the
document builder and the Jupyter kernel, is pinned in `pyproject.toml`.

The document itself (`index.md`) explains the loop. In short:

| | |
| --- | --- |
| `make preview` | live preview, with staleness that updates as you work |
| `make edit` | open the authored diagram in draw.io |
| `make status` | has a panel changed since it was embedded? |
| `make check` | is every panel identified well enough to publish? |
| `make break` | change the data so the figure goes stale |
| `make refresh` | re-embed changed panels and re-render |
| `make site` | build the document |

`calkit run` does the whole chain: plot → embed → render → check → build.

## Source and output are two files

| File | Role |
| --- | --- |
| `figures/composite.drawio` | **Source.** Edit this. No stage writes it. |
| `figures/composite.drawio.svg` | **Derived.** The pipeline renders it. Do not edit. |

A `.drawio.svg` is genuinely both viewable and editable, so it is tempting to use
one file for both roles — and this example did, until the pipeline was actually
run. DVC rejected it: the file was a dependency of `embed-figure` and the output
of `render-figure`, which is a file built from itself. The rejection is correct.
A single file would mean every pipeline run silently overwrote whatever had been
drawn since the last one.

## Live preview

`make preview` runs two things, and both are needed:

- `myst start --execute`, the MyST dev server.
- `figmint watch`, which touches `index.md` when a tracked component changes,
  and serves the endpoint behind the panel's edit button.

The watcher exists because MyST caches the parsed page. It rebuilds the site
whenever a file changes, but it will not re-run the `:::{figmint}` directive
unless the markdown itself changed — so a regenerated panel would leave the
provenance panel confidently reporting stale information. Touching `index.md`
invalidates that cache, which is the whole trick.

### Why the edit button is an HTTP link

The obvious way to open a diagram from a rendered page is `vscode://file/…`, and
that works fine in Chrome, where the OS hands the URL to VS Code. It does not
work in VS Code's own Simple Browser, which is where this preview is most likely
to be read: a webview will not follow a non-http scheme, so the link just
navigates and shows a blank page.

A webview *will* follow `http://localhost`. So `figmint watch --open-server`
serves one route on loopback that opens the file locally and answers `204 No
Content` — which browsers treat as "stay where you are", making the link behave
like a button instead of navigating away.

The link only appears when `FIGMINT_OPEN_URL` is set, which `make preview` does.
A published build names the source path instead, rather than carrying a dead
link to a port on the author's laptop. It is deliberately not a markdown link to
the source: MyST copies a linked project file into the build under a
content-hashed name, so clicking it would hand the reader a duplicate — and
editing that duplicate is lost work. Set `FIGMINT_EDITOR` to override what opens
the file; otherwise it prefers `code -r` and falls back to `drawio`.

## Optional: the provenance graph

`index.md` sets `:graph: true` on the figure, which renders a Mermaid diagram of
where it came from — script, packages, generation edge. That needs Stencila's
Python SDK:

```sh
uv sync --group stencila
```

It is a separate group because installing it compiles a slice of Stencila's Rust
workspace — minutes on a cold cache — but it is listed in `default-groups`,
which it has to be: `uv run` re-syncs the environment to the default groups, so a
non-default group is uninstalled the moment `calkit run` invokes a stage through
`calkit xenv`, and the published document loses its graph without saying why.

Drop `stencila` from `[tool.uv] default-groups` to make the example install fast
again. The panel then reports the graph as unavailable and nothing else
changes.

## ASTRA, and MySTRA

`astra.yaml` records the methodological decisions — the polynomial degree, how
the peak is located, whether the low-λ tail is cut — each with its alternatives
and reasoning. Switch between them:

```sh
make universes            # list them
make universe U=cubic_fit # select one and rerun the pipeline
```

[MySTRA](https://github.com/LightconeResearch/MySTRA) is the official MyST
plugin for ASTRA, and `index.md` uses it to pull the decisions and the Betz-limit
insight straight out of the spec instead of restating them. It sits alongside
figmint's plugin in `myst.yml`; the two answer different questions and do not
overlap.

| | Answers | Reads |
| --- | --- | --- |
| MySTRA | which decisions, which option is selected, why | `astra.yaml`, `universes/` |
| figmint | which bytes are in the figure, from where, still current | the diagram, `calkit.yaml`, `dvc.lock` |

Two rough edges, both from MySTRA being pre-1.0 (it says so itself — the plugin
is pinned to `v0.0.7` rather than tracking `latest`):

- **It resolves the first universe in `universes/`** and offers no documented way
  to select another. So `make universe U=cubic_fit` reruns the pipeline under the
  cubic fit while the rendered decision block still reads "selected: Quartic".
  Alphabetical order makes `baseline` the one shown, which is right by accident.
- **Output embeds need MySTRA's results layout**, `results/<universe>/<id>/<id>.<ext>`.
  This project writes `results/peak.json`, so `:::{astra} outputs.cp_fit` renders
  the card without the artifact. It degrades quietly rather than failing. Figures
  here are figmint's job anyway, which is the sharper division.

A third edge is MyST's, not MySTRA's: the execution cache is keyed on the
*content* of the markdown, so it never notices that `results/peak.json` changed
and the numbers quoted in the prose keep reporting the previous universe.
`touch index.md` does not help — the hash is unchanged. `make universe` clears
`_build/execute` for that reason.

## What this example is demonstrating

- **Provenance in the document, not just the terminal.** The `:::{figmint}`
  directive renders the figure *and* a panel saying where each component came
  from, whether it is current, and whether any of it was machine-generated. It
  is an executable MyST plugin (`figmint-myst`), so the document is built by the
  same code that answers `figmint check` rather than a reimplementation of it.
- **Three kinds of figure, with different guarantees.** A pipeline output
  (`reproducible`), an imported AI-generated image (`signed`, and disclosed as
  machine-generated by its C2PA credentials), and a figure computed by a
  `{code-cell}` in the document itself — which needs no tracking at all, because
  the code that made it is right there.
- **Provenance the script doesn't have to know about.** `scripts/plot_cp.py` is
  an ordinary plotting script; the pipeline stage that runs it is what makes the
  output `reproducible`.
- **Two kinds of stale, kept apart.** A changed panel is a different question
  from an unidentified one, and they have different fixes.

## Provenance failure modes

1. Imported component with no provenance (DOI or signed origin).
2. Component out-of-date.
3. Composite out-of-date.
