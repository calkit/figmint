# figmint + Quarto

A small document whose figure carries its own provenance.

```sh
uv sync
make extension   # install the Quarto filter into _extensions/
make declare     # say where the data and the script came from
make plot        # produce the figure, recording where it came from
make status      # is anything stale?
make site        # build the document, recording it too
```

`make help` lists the rest. Quarto itself is not a Python package here — install
it from [quarto.org](https://quarto.org/docs/get-started/) and make sure
`quarto` is on your `PATH`.

Unlike the MyST example, nothing generated is committed: run `make all` once
and `figmint.toml`, the figure and the site appear.

## What to look at

`figmint.toml` — the record. One artifact per section, each with the SHA256 of
its bytes, the hashes of everything it was derived from, and the command that
produced it. Read the header before touching it.

`index.qmd` — the document. The `::: {.figmint}` blocks render the record into
the page, so a reader gets the provenance without leaving the figure.

`_quarto.yml` — where the filter is wired in. Note `at: pre-ast`: Quarto builds
callouts, figure numbers and cross-references in its own filters, so a panel
emitted after them is a grey box under an unnumbered picture.

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
