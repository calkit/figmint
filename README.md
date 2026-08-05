# 🌿 Figmint

Figmint provides artifact provenance tracking, including support for
composite artifacts like figure PNGs, publication PDFs,
static notebook and website HTML, machine learning models, and more,
which can be traced all
the way back to their primary inputs, e.g., scripts or images generated
with AI tools.
This helps authors and readers know exactly how an output was created,
so they can assess if it's trustworthy.

Figmint outputs are signed and contain Content Credentials metadata.
All of a project's artifact provenance information lives inside a
`figmint.toml` file, which includes a comment at the top to prevent AI agents
from tampering with the provenance information, which may indicate
falsified evidence in scientific projects.

## Installation

```sh
uv tool install figmint
```

## Usage

If you have a script that generates a figure, run it through the Figmint CLI:

```sh
figmint run -i data/raw.csv -o figures/plot.png -- uv run plot.py
```

The command (what comes after `--`) must be run with an environment manager
that uses lock files so those can be tracked as part of the provenance,
since they are important input information.
Practically this means that the command must start with one of the following:

- `uv run` (as long as a `uv.lock` file will be created)
- `pixi run`
- `bun run`
- `cargo run`
- `nix develop --command` (as long as a `flake.nix` file is present)
- `calkit xenv` (use for Docker, Conda, Julia, renv environments)
- `calkit nb exec`
- `calkit latex build`

### Declaring primary artifacts

Every chain ends somewhere. Follow a figure back far enough and you reach a
file figmint did not make: measurements typed into a CSV, a dataset someone
downloaded, a plotting script. Left alone those sit at the bottom of the chain
unexplained, and `figmint status` warns about them, because every automated
check above them passes — which is exactly what makes the gap easy to miss.

Declare them with:

```sh
figmint declare <path> --mine
```

This records your "attestation". AI agents should similarly declare
when they've created primary artifacts so readers can assess if that
is an acceptable use of generative AI — but an agent cannot be the one
answering for the file, so the tool is disclosed *beside* a person rather
than in place of one:

```sh
figmint declare <path> --mine --with-ai 'Claude Opus 5'
figmint declare <path> --author 'A Researcher' --with-ai 'Claude Opus 5'
```

Using `--with-ai` without naming a person is refused.

Both flags repeat, because an artifact rarely has exactly one author and code
almost never does — a script grows through several hands and, increasingly,
several models:

```sh
figmint declare <path> --author 'A Researcher' --author 'A Colleague' \
    --with-ai 'Claude Opus 5' --with-ai 'GitHub Copilot'
```

Git already knows most of this, so it can be read out instead of retyped.
Every commit touching the file names an author, and `Co-authored-by:` trailers
name everyone else — which is exactly where an agent's own signature lands:

```sh
figmint declare <path> --from-git-history
```

Whether an author is a person or a tool is a guess when it comes from git, so
`figmint declare` prints what it decided and `--author`/`--with-ai` override it.

Hand-authored artifacts have authors too. A `.drawio` canvas is assembled from
recorded panels but arranged by people and agents, so it carries a derivation
chain *and* an author list; declaring it adds the authors without disturbing
its inputs.

An attestation is the weakest thing in the record: nothing verifies it, and
figmint says so wherever it is displayed. When the file came from somewhere a
reader could actually fetch, say that instead:

```sh
figmint declare <path> --doi 10.5281/zenodo.1234567
figmint declare <path> --git github.com/myuser/myproject/path/to/data.csv@{git_rev}
figmint declare <path> --calkit calkit.io/myuser/myproject/path/to/data.csv@{git_rev}
```

The revision is required. `github.com/myuser/myproject/data.csv` names whatever
is at that path today — a mutable claim wearing the costume of a citation.
The `--calkit` form exists because Calkit tracks large files with DVC, so a
path in a Calkit project can name data that is not in the git tree at all.

Scripts count too. A figure resting on a script nobody will claim is as
unplaceable as one resting on data nobody collected, and a script a model wrote
is exactly what a reader needs told.

A declaration is about *people*, not bytes, so editing a declared file does not
invalidate it and never needs redoing. `figmint declare` records a hash too,
but only as a note of what was seen — nothing checks it. `figmint run` keeps it
current whenever it uses the file, so the record does not drift, and says so
rather than doing it quietly:

```
recorded figures/cp_curve.png (signed)
   in   data/performance.csv
   in   scripts/plot_cp.py
   refreshed hash for scripts/plot_cp.py (declared; authorship unchanged)
```

Only declarations are eligible. A hash figmint wrote itself is evidence, and
rewriting that is the tampering the record exists to catch — so an artifact
with a command keeps reporting `modified` no matter how many later runs
consume it.

### Where AI disclosure lives

A file that carries Content Credentials already says whether it is
machine-generated, signed by whoever made it — the example's AI-generated
figure really does carry Google's own "Created by Google Generative AI". There
is no need to restate that in `figmint.toml`, and figmint reads it from the
file instead.

But a manifest is fragile: any tool that re-encodes the bytes silently discards
it, which is the whole reason `figmint drawio import` exists. And most formats
cannot carry one at all — a `.py`, a `.csv`, a PDF, or a notebook's HTML have
nowhere to put it. So the record holds the disclosure whenever the file cannot,
and where both exist figmint compares them and **reports the disagreement**
rather than quietly picking a winner. A disclosure that evaporates the first
time somebody opens the image in an editor is exactly the failure worth seeing.

### What can be signed

Reading credentials and writing them are different questions, and the second
set is smaller. Verified against c2pa-python 0.37.4 (c2pa-rs 0.90.4), figmint
can **embed** a manifest in PNG, JPEG, GIF, SVG, TIFF, WebP and WAV.

**PDF is the case worth knowing about.** The C2PA specification covers PDF, and
other tools do sign them — but c2pa-rs cannot yet write one, though it reads
them perfectly well. So a PDF signed elsewhere will have its credentials read
and displayed by figmint; a PDF figmint produces is recorded but unsigned. That
is a limitation of the library, not of the format, and it should lift on its
own.

HTML is not applicable: c2pa does not recognise the type at all, in either
direction. There is nowhere in an HTML page for a manifest to live.

This matters for whole-document outputs — a built paper is exactly the artifact
you would want to hand someone with its provenance attached. So for a MyST PDF,
for `calkit latex build`, and for the HTML a notebook renders to, figmint
records the artifact in `figmint.toml`, skips signing rather than failing the
build, and says which it did:

```
recorded paper.pdf (not signed: .pdf cannot carry Content Credentials;
                    the record in figmint.toml is its only provenance)
```

For those outputs the record is the only provenance there is, which is why the
warning at the top of `figmint.toml` is not decoration.

### Freshness checking

To see if a given output's inputs (including environment lock files) have
changed, rendering it stale, use the `status` command.

```sh
figmint status <path>
```

The states are kept apart, because they have different fixes:

- **stale** — an input changed; regenerate it.
- **modified** — a *produced* output changed without going through figmint, so
  the record no longer describes the file it names. This is how tampering with
  an output after generation is caught.
- **upstream** — sound in every direct link, but resting on one that is not.

A **declared** artifact is never checked against its own hash, and this is the
rule worth stating plainly, because everything else follows from it:

> A declaration says who is answerable for a file, not what it contained on
> some particular afternoon. What matters about an edit is whether it reached
> an output — and that is already recorded, because every output carries the
> hash of each input as it was when the output was made, recomputed whenever
> `figmint run` regenerates it.

So editing a script, a dataset, or a document source is not a finding and never
requires re-declaring anything. The consequence shows up where it can be acted
on: on the outputs built from the old bytes. The honest cost is that a declared
file nothing consumes — no output to go stale — is not watched at all; if it is
swapped, nothing notices.
- **upstream** — this artifact is sound in every direct link but rests on one
  that is not. Staleness does not stop at the first link: a composite whose
  `.drawio` is untouched passes every direct check even when the data three
  steps back was edited, because nothing regenerated the panel in between.

None of them is repaired by editing `figmint.toml`, which is the one thing a
reader in a hurry might try.

### Rebuilding

Every artifact records the command that made it and the inputs it was made
from, so the repair sequence is a property of the record rather than something
you have to reconstruct:

```sh
figmint rebuild             # everything that is out of date
figmint rebuild <path>      # that artifact, and everything behind it
figmint rebuild --dry-run   # say what would happen, in order
```

Dependencies first, because rebuilding a document before the figure it embeds
accomplishes nothing and you would end up running everything twice. Each
command is repeated in-process rather than through a shell, so nothing depends
on quoting the record was written to avoid.

Nothing is rebuilt that does not need it — an artifact whose inputs still hash
to what the record says keeps its bytes and its signature. Anything that cannot
be rebuilt is named rather than passed over: nobody can regenerate raw data,
and a hand-arranged diagram is refreshed by the export that consumes it.

### Signing certificates

The `figmint run` command includes the `--cert` option to provide a signing
certificate for the Content Credentials metadata.

### draw.io

Figmint enables composite figure provenance while retaining interactive
editing with draw.io.
To import a PNG into a draw.io diagram, run:

```sh
figmint drawio import my-figure.png my-composite-figure.drawio
```

It's important to use the Figmint CLI since this will embed metadata into
the `.drawio` file.

`import` is an *authoring* step: it puts a new panel on the canvas. You should
not need to run it again when a figure is redrawn, and you don't — each shape
records the `src` it came from and the hash it had, so the export below
re-embeds any panel that has moved on, keeping the position and size you gave
it, and says which ones it touched. Re-running `import` on a panel already
present is still safe: it replaces rather than adding a second copy.

To export an SVG containing the provenance information, run:

```sh
figmint drawio export my-diagram.drawio my-diagram.svg
```

Export is the build step. It refreshes stale panels first — draw.io renders the
*copy* inside the diagram, so refreshing afterwards would publish the old
pictures — then renders, signs the result with every panel as an ingredient,
and re-records the diagram itself.

### GIMP

To export a PNG from GIMP with provenance tracking, run:

```sh
figmint gimp export my-input.xcf my-output.png
```

### MyST

Figmint includes a MyST plugin for inspecting and checking embedded figure
provenance and freshness.
It also enables inspecting document-level provenance, since that's a
composite artifact itself.

The directive is not only for figures. Point it at a `.csv` or `.tsv` and it
renders the numbers as a table — numbered and cross-referenceable like any
other, with the same provenance panel underneath:

```
:::{figmint} data/performance.csv
:name: tbl-performance
:rows: 25
The measurements underlying [](#fig-performance).
:::
```

That is where the record earns the most, because a CSV cannot carry Content
Credentials at all: the line in `figmint.toml` is its only provenance. Long
tables are truncated at `:rows:` (25 by default) with a note saying so — a
table is for reading, and a thousand rows of it is a scroll bar.

Anything that is neither a picture nor a table renders as its filename with the
panel attached, and the panel names what it is looking at: *Figure*, *Table*,
or *Artifact*.

The document-level panel takes an `:artifact:` option naming the document's own
output(s):

```
:::{figmint-provenance}
:artifact: _build/html/index.html, _build/exports/paper.pdf
:table: true
:graph: true
:::
```

Naming them does two things. It shows the command that rebuilds the document,
read from the record so it cannot drift from what actually produced the file.
And it leaves those outputs out of the panel's own freshness tally — a document
cannot honestly report on itself from the inside, because while the page is
being written its recorded hash still describes the previous build. Without
this the panel reads "out of date" on every single build and stops meaning
anything. `figmint status` checks them from outside, where the answer is
settled.

The table lists **outputs** — anything the project made, including
intermediates like a `.drawio` — and for each one names the input responsible
when it is behind:

| Output | Built from | State |
| --- | --- | --- |
| `figures/cp_curve.png` | 3 | ⚠️ `scripts/plot_cp.py` changed |
| `figures/composite.svg` | 1 | ⚠️ waiting on `figures/cp_curve.png` |

Sources are what those answers point at, not rows of their own. Asking whether
a plotting script is "up to date" has no answer — nothing produces it — and
listing it green above the figure it just broke is the confusion this avoids.

One caveat about live preview. `myst start` re-renders when one of *its own*
sources changes: markdown, `myst.yml`, a linked image. Editing a script or a
dataset is invisible to it, so the panels keep showing the previous render and
a stale figure looks current for as long as the tab is open. The example's
`make serve` works around this by watching everything figmint records as an
input and touching the document when any of it moves.
