---
title: Figmint tracks artifact provenance
authors:
  - name: Example Author
---

# Figmint tracks artifact provenance

A small document whose figure carries its own provenance. The panel below was
produced by a script figmint knows nothing about; what figmint knows is what the
script was given, what environment it ran in, and what came out.

## The figure

:::{figmint} figures/composite.svg
:name: fig-performance
:width: 95%

(a) Power coefficient against tip speed ratio, measured in a towing tank —
synthetic values for this example. (b) An illustrative schematic of the machine,
generated with an AI image model and imported.
:::

Expand the panel and you get what a reader would otherwise have to take on
trust: what this picture was made from, whether it is still current, and — from
the embedded manifest — that part of it is machine-generated. All of it is read
from `figmint.toml` and the file's own credentials when the document builds.
Nothing on this page is typed by hand.

That last point is the one worth dwelling on. The schematic is AI-generated, and
figmint did not have to be told: the image carries C2PA credentials saying so,
and the export propagates the disclosure into the composite. A reader who is
handed only `composite.svg`, with no repository, can still establish it.

## How it got there

```sh
figmint run -i data/performance.csv -o figures/cp_curve.png \
    -- uv run python scripts/plot_cp.py
```

figmint hashed the inputs, ran the command, hashed the output, and recorded all
of it. It did not read the script, and it does not need to.

figmint also recorded `scripts/plot_cp.py`, which was never passed with `-i`.
Any file the command names is hashed too — forgetting the script is the easiest
mistake to make and among the worst to live with, since it is the input most
likely to change and its omission would leave the figure looking current after
the code that drew it was rewritten.

### The environment is an input

The command has to go through a manager with a deterministic lock file — here
`uv run`, so `uv.lock` is recorded alongside the CSV. That is not bookkeeping.
The same script under a different matplotlib draws a different figure, and a
record naming only the data would call that unchanged.

Try it: change a dependency, re-lock, and the figure is stale before you have
touched a line of code or a byte of data.

### Going stale

```sh
make break     # edits the data
figmint status # the figure is now out of date
```

The check compares the hash of every recorded input against the file on disk.
There is no cache to invalidate and no timestamp to be confused by; either the
bytes match what was recorded or they do not.

:::{warning}
`figmint status` reporting a figure as stale means the figure is stale. The fix
is to regenerate it — never to edit the hashes in `figmint.toml`. That file is
evidence, and a record edited to match files it does not describe is
indistinguishable from a forged one.
:::

## How the composite was assembled

A figure made of several panels is an artifact whose inputs happen to be other
artifacts:

```sh
figmint drawio import figures/cp_curve.png figures/composite.drawio
figmint drawio import figures/turbine.png  figures/composite.drawio
# ...arrange it in draw.io, add the definition of Cp...
figmint drawio export figures/composite.drawio figures/composite.svg
```

Each image goes in byte-for-byte, carrying `src` and `hash` on its shape, and
the diagram is recorded with the panels as inputs. Regenerating a panel makes
the composite stale — which nothing inside draw.io could ever notice, since it
holds a *copy* with no link back.

Use the figmint command rather than draw.io's own **Insert → Image**. draw.io
resizes anything over 1200 px through a canvas before embedding it, which
re-encodes the bytes and destroys any Content Credentials the image carried,
including the AI disclosure on the schematic above.

### Why export is a separate step

A `.drawio` is a source; it is not a picture anything but draw.io can display.
`figmint drawio export` renders one, and does three things while it is there:

- exports with `--embed-diagram`, so the shapes' `src` and `hash` survive into
  the SVG and the published file can still be checked;
- **re-records the diagram**, because editing it in draw.io is the entire point
  of keeping one and every edit changes its bytes. Without this the composite
  would sit permanently `modified`, and that signal — *somebody wrote this file
  behind figmint's back* — would be worth nothing. Exporting is the deliberate
  act that says "this arrangement is the one I meant";
- signs the result, naming both the diagram and its panels as ingredients. The
  panels matter: a `.drawio` carries no manifest of its own, so without them the
  schematic's AI disclosure would stop at the diagram and the exported figure
  would claim to be an ordinary composite.

## The whole document

A document is a composite too — of every artifact it embeds. The graph is the
record itself, drawn: `figmint.toml` is already a DAG, so nothing here can
disagree with the freshness reported beside it.

:::{figmint-provenance}
:table: true
:graph: true
:::

Read it right to left and you have the answer to "where did this figure come
from": a CSV and a lock file make the plot, the plot and an imported schematic
make the diagram, the diagram makes the published picture. Rounded nodes are raw
inputs — nothing in the project produced them, which for `turbine.png` is
exactly the fact its credentials disclose.

## Where the chain ends

Follow any figure back far enough and you reach a file figmint did not make.
Here that is `data/performance.csv` — measurements — and `figures/turbine.png`,
an image generated with an AI tool. `figmint run` cannot record those, because
figmint was not there.

Left alone they are the hole in the middle of an otherwise checkable record: a
figure that is current in every link, resting on data nobody can place. Every
check above them passes, which is what makes the gap easy to miss. `figmint
status` says so:

```
warning: nothing accounts for data/performance.csv
         declare it: `figmint declare <path> --mine [--with-ai ...]`,
         `--doi ...`, or `--git ...@rev`
```

So a primary artifact is *declared*, and the declaration says which kind of
claim it is:

```sh
figmint declare data/performance.csv --mine
figmint declare data/other.csv --doi 10.5281/zenodo.1234567
figmint declare data/shared.csv --git github.com/user/proj/data.csv@a1b2c3d
figmint declare data/dvc.csv --calkit calkit.io/user/proj/data.csv@a1b2c3d
```

`--mine` is an attestation — nothing verifies it, and figmint says so when you
make it. A DOI resolves to something a reader can fetch. `--git` and `--calkit`
name a location **pinned to a revision**, which is not decoration: a path
without one points at whatever is there today, a mutable claim wearing the
costume of a citation. `--calkit` exists because Calkit tracks large files with
DVC, so a path there can name data that is not in the git tree at all.

Scripts count too, and so does generative AI. `figures/turbine.png` and
`scripts/plot_cp.py` in this project were both made with a model, which is
disclosed *beside* an accountable person rather than in place of one — a model
can produce a file but cannot answer for it:

```sh
figmint declare figures/turbine.png --mine --with-ai 'Google Gemini'
figmint declare scripts/plot_cp.py --mine --with-ai 'Claude Opus 5'
```

Both show up in the provenance panels above as *created by Pete Bachant with
Google Gemini* and *…with Claude Opus 5*, so a reader can judge whether that is
an acceptable use here.

The declaration lives in `figmint.toml` beside the artifact's hash. There is no
separate signature: putting the same claim in a sidecar would be exactly as easy
to delete as the line it duplicates, and would look like a cryptographic
guarantee without being one.

## Content Credentials

Outputs are signed as they are produced, so the provenance travels with the file
once it leaves the repository. `figmint.toml` is the record while the figure is
here; the embedded manifest is what a reader has after it has been pasted into a
manuscript and mailed to a co-author.

The manifest names each input as an ingredient and carries an IPTC
`digitalSourceType`. Where any input declares generative-AI origin, that becomes
`compositeWithTrainedAlgorithmicMedia` — so "does this figure contain
AI-generated material?" is answered by the signature rather than by a convention
somebody has to remember to follow.
