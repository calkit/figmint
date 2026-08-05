# figmint + MyST

A small document whose figure carries its own provenance.

```sh
uv sync
make plot        # produce the figure, recording where it came from
make composite   # embed it in a draw.io diagram
make status      # is anything stale?
make serve       # read the document
```

`make help` lists the rest.

## What to look at

`figmint.toml` — the record. One artifact per section, each with the SHA256 of
its bytes, the hashes of everything it was derived from, and the command that
produced it. Read the header before touching it.

`index.md` — the document. The `:::{figmint}` blocks render the record into the
page, so a reader gets the provenance without leaving the figure.

## The loop

```sh
make break     # change the data
make status    # the figure is now stale, and so is the composite
make plot      # regenerate
make restore
```

Nothing here is a timestamp comparison. Either the bytes hash to what was
recorded or they do not.

## Why the command needs an environment manager

`make plot` runs the script through `uv run`, so `uv.lock` is recorded as an
input alongside the CSV. A record naming only the data would call the figure
unchanged after a dependency upgrade that visibly redrew it.
