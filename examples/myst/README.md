# figmint + MyST example

A playground for the editing loop: a MyST document whose figure is a draw.io
diagram, with figmint tracking where each panel came from and whether any of
them has gone stale.

```sh
uv sync          # figmint (editable, from ../..), matplotlib, mystmd
make help
```

One prerequisite lives outside the environment: **draw.io**, used to render the
diagram and to open it for editing. It is a desktop app, so it cannot be a
project dependency — `brew install --cask drawio`, or run that stage in a
container if you need the pipeline fully hermetic. Everything else, including the
document builder, is pinned in `pyproject.toml`.

The document itself (`index.md`) explains the loop. In short:

| | |
| --- | --- |
| `make edit` | open the figure in draw.io |
| `make status` | has a panel changed since it was embedded? |
| `make check` | is every panel identified well enough to publish? |
| `make break` | change the data so the figure goes stale |
| `make refresh` | re-embed changed panels and re-render |
| `make site` | build the document |

`calkit run` does the whole chain: plot → embed → render → check → build.

## What this example is demonstrating

- **One file per figure.** `figures/composite.drawio.svg` is the published
  picture, the editable draw.io source, and the provenance record at once.
- **Provenance the script doesn't have to know about.** `scripts/plot_cp.py` is
  an ordinary plotting script; the pipeline stage that runs it is what makes the
  output `reproducible`.
- **Two kinds of stale, kept apart.** A changed panel is a different question
  from an unidentified one, and they have different fixes.
