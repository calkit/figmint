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
:artifact: _build/html/index.html
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

Scripts count too, and so does generative AI. A model can produce a file but it
cannot answer for one, so a tool is named *alongside* an accountable person
rather than instead of one, and both flags repeat — an artifact rarely has
exactly one author and code almost never does:

```sh
figmint declare scripts/plot_cp.py --mine --with-ai 'Claude Opus 5'
figmint declare figures/composite.drawio --mine --with-ai 'Claude Opus 5'
```

The `.drawio` canvas is in that list because it is assembled from recorded
panels but *arranged* by hand, by a person and an agent together. It has a
derivation chain and an author list at the same time, and declaring the authors
leaves its inputs untouched.

Where git already knows the answer, it can be read out instead of retyped —
including the `Co-authored-by:` trailers, which is where an agent's own
signature lands:

```sh
figmint declare scripts/plot_cp.py --from-git-history
```

### One fact, one place

`figures/turbine.png` was generated with Google Gemini, and it is *not*
declared with `--with-ai`. It does not need to be: the PNG carries Google's own
Content Credentials saying "Created by Google Generative AI", signed by the
people who made it. Restating that in `figmint.toml` would create a second copy
that can drift from the first. The panel above reads it from the file, and says
so — *AI-generated, per the file's own credentials*.

`scripts/plot_cp.py` is the opposite case. A `.py` file has nowhere to put a
manifest, so the record is the only place its disclosure can live, and the
panel says *per the record*.

When both exist they are compared, and a disagreement is reported rather than
resolved. That matters because a manifest is fragile — any tool that re-encodes
an image silently discards it, which is the whole reason `figmint drawio
import` exists. A disclosure that evaporates the first time somebody opens the
figure in an editor is exactly the failure worth seeing.

The declaration lives in `figmint.toml` beside the artifact's hash. There is no
separate signature: putting the same claim in a sidecar would be exactly as easy
to delete as the line it duplicates, and would look like a cryptographic
guarantee without being one.

Editing a declared file does not invalidate the declaration, and never needs it
redone. A declared artifact is not checked against its own hash at all: nothing
produced it, so a change means a person edited it, and what matters about that
is whether the edit reached an output — which the input hashes on every output
already record. `figmint run` keeps the declared hash current as it goes, so
the record does not drift.

A hash figmint wrote *itself* is a different matter. That is evidence, it is
checked, and no amount of later runs will quietly rewrite it.

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

## The document is an artifact too

Nothing in the build re-imports a panel. `figmint drawio import` is how a
figure gets *onto* the canvas in the first place; after that the diagram knows
where each panel came from, and `figmint drawio export` re-embeds any that have
been redrawn before it renders — so a changed figure reaches the composite
without anybody being asked to say so.

`make all` builds whatever is out of date, in dependency order — the Makefile's
targets are real files with real prerequisites, mirroring the derivation graph
figmint records. `make site` runs the build through `figmint run`, so the
rendered document is recorded like any other output — with `index.md`,
`myst.yml` and the composite figure as its inputs. That makes "is this HTML
still consistent with everything behind it?" a question `figmint status` can
answer, which is the one a reader most wants answered and the one nothing else
checks.

Two kinds of circularity had to be kept out of the way. `figmint.toml` is
deliberately *not* an input of the document, even though this plugin reads it
to draw the panels above — recording the file that `figmint run` itself writes
would make the document stale the instant it finished building.

The subtler one is that a document cannot honestly report on its *own*
freshness from inside itself. While this page is being written the hash in the
record still describes the previous build, so the panel above would call itself
out of date on every render — permanently, and in the one place nobody can act
on it. That is what the `:artifact:` option is for: it names this document's
output so the panel can show how to rebuild it and leave it out of its own
tally. `figmint status` checks it from outside, where the answer has settled.

One consequence is worth knowing about while writing. `myst start` re-renders
when *its own* sources change — this file, `myst.yml`, the images it links. A
change to `scripts/plot_cp.py` is invisible to it, so the panels above keep
showing the state from the last render even though the figure is now stale.
`figmint status` is the live answer; the panels are a snapshot of the moment
the page was built.

The HTML is recorded but **not signed**: c2pa does not recognise the type at
all, so the page you are reading cannot carry a manifest under any tool. The
same applies to the HTML a notebook renders to. For those outputs
`figmint.toml` is the only provenance there is, which is exactly why the
warning at the top of it is not decoration.

<!-- preview edit -->
