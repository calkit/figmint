# figmint + Quarto

A small document whose figure carries its own provenance.

```sh
uv sync
make extension   # install the Quarto filter into _extensions/
make declare     # say where the data, the scripts and the diagram came from
make plot        # produce the two panels, recording where they came from
make composite   # export the multi-panel figure from the draw.io diagram
make status      # is anything stale?
make site        # build the document, recording it too
```

`make all` does the lot. `make help` lists the rest.

Two things are not Python packages and have to be installed yourself: **Quarto**
([quarto.org](https://quarto.org/docs/get-started/)) and, for the composite,
the **draw.io desktop app** ([drawio.com](https://www.drawio.com/)). Both need
to be on your `PATH`.

**`figmint.toml` is committed**, along with every figure it describes and the
lock file it names. That is not incidental to the example: the record is the
evidence, and a project that ignores its own record is one where nobody can
check anything without rebuilding first. Clone this and `figmint status` has an
answer immediately.

`figures/composite.drawio` is committed for a different reason — a diagram is a
source, the arrangement of the panels is the work, and nothing regenerates it.

The rendered site is the one thing left out, as in the MyST example. So on a
fresh clone `figmint status` reports `_site/index.html` as **missing** until you
run `make all`, which is the record correctly noticing an artifact that is not
there rather than a flaw in the example.

## What to look at

`figmint.toml` — the record. One artifact per section, each with the SHA256 of
its bytes, the hashes of everything it was derived from, and the command that
produced it. Read the header before touching it.

`index.qmd` — the document. The `::: {.figmint}` blocks render the record into
the page, so a reader gets the provenance without leaving the figure. There is
one block for a composite figure, one for a single panel, one for the CSV
behind them, and one with no `src` at all, which describes the document.

`_quarto.yml` — where the filter is wired in. Note `at: pre-ast`: Quarto builds
callouts, figure numbers and cross-references in its own filters, so a panel
emitted after them is a grey box under an unnumbered picture.

## The chain

```
performance.csv ─┬─> plot_cp.py ─> cp_curve.svg ─┐
                 │                               ├─> composite.drawio ─> composite.svg ─> index.html
        uv.lock ─┴─> plot_ct.py ─> ct_curve.svg ─┘
```

Three levels deep on purpose. A composite's only _direct_ input is the diagram,
which tells a reader nothing, so the panel lists the whole transitive chain —
and staleness does not stop at the first link: edit `plot_ct.py` and the
composite is out of date even though the diagram it came from was never
touched.

## The loop

```sh
make break     # change the data
make status    # the figure is now stale, and so is the document
make all       # regenerate, in dependency order
make restore
```

Nothing here is a timestamp comparison. Either the bytes hash to what was
recorded or they do not.

## Why the command needs an environment manager

`make plot` runs the script through `uv run`, so `uv.lock` is recorded as an
input alongside the CSV. A record naming only the data would call the figure
unchanged after a dependency upgrade that visibly redrew it.
