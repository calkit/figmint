# fromwhere + MyST

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

`provenance.toml` — the record. One artifact per section, each with the SHA256 of
its bytes, the hashes of everything it was derived from, and the command that
produced it. Read the header before touching it.

`index.md` — the document. The `:::{fromwhere}` blocks render the record into the
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

## Reproducibility gotchas

Two things this project does not pin, stated rather than glossed.

**Node.js.** `mystmd` on PyPI is a Python wrapper around a JavaScript CLI. The
JS itself ships inside the wheel, so the _MyST version_ is pinned by `uv.lock` —
but the runtime under it is not: `mystmd` calls `shutil.which("node")` and uses
whatever it finds. Worse, on a machine with no Node it stops and _asks_
interactively whether to install one, which in CI is a build that hangs rather
than one that fails. Set `MYSTMD_ALLOW_NODEENV=1` to answer that question
up front, or install Node yourself, or do what the Quarto example does and use
pixi, which has `nodejs` on conda-forge and would put it in the lock.

**draw.io.** The composite needs the draw.io desktop app on `PATH`. It is an
Electron application with no conda or PyPI package, so no lock file can pin it,
and `figures/composite.svg` rests on a tool version the record cannot name.

Both are instances of one thing: fromwhere records the lock of the environment the
_command_ ran in, and a tool that wraps the command from outside is not in that
lock. See the note in the top-level README.
