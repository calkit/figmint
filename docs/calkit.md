# Use within a Calkit project

Calkit and Figmint have different jobs.
Calkit is the overall research project manager with its own environment-aware
workflow/pipeline system built upon DVC,
and Figmint is dedicated to provenance tracking for primary, derived,
and composite artifacts.

Both touch "what came from what", so it is worth being precise about which
one owns what, and where the two must not overlap.

## Division of responsibility

**Calkit owns execution and the graph.** It decides what to run, in what
order, and what can be skipped.
DVC is genuinely better at this than Figmint could be: caching, remote
storage, partial reruns.

**Figmint owns evidence.** SHA256 hashes as an attestable record,
declarations of authorship and generative-AI involvement, Content Credentials
embedded in outputs, and rendering all of that into the published document.
Calkit has none of this, and adding it would mean maintaining two answers to
the same question.

The redundancy people notice first is that `dvc.lock` and `figmint.toml` look
alike.
They do—`dvc.lock` records `cmd`, `deps`, and `outs` with hashes, including
the environment lock file, which is the same model.
The difference is what the hashes are *for*, and that difference is load
bearing.

## How a stage uses Figmint

A Calkit stage wraps its command:

```yaml
pipeline:
  stages:
    plot-cp:
      kind: python-script
      script_path: scripts/plot_cp.py
      environment: main
      figmint: true
```

which compiles to a stage command of roughly the shape

```sh
figmint run -i data/performance.csv -o figures/cp_curve.png \
    -- calkit xenv -n main -- python scripts/plot_cp.py
```

`calkit run` remains the project entrypoint.
Figmint wraps the *stage command*, not Calkit itself.
Wrapping Calkit would make Figmint the entrypoint, which is the thing this
arrangement exists to avoid.

Because Calkit compiles the wrapper from the stage definition, the `-i` and
`-o` flags are not written twice and cannot drift from the stage's declared
inputs and outputs.

### Why the wrapper has to be inside the stage

Figmint signs an artifact *before* hashing it, because embedding a C2PA
manifest changes the bytes.
If DVC hashed an output and Figmint signed it afterward, the hash in
`dvc.lock` would be wrong the moment signing finished, DVC would see the
output as modified, and the stage would rerun forever.

So signing has to happen inside the stage, before DVC ever looks at the file.
That rules out "run the pipeline, then sign" and it rules out Figmint wrapping
Calkit, since the signing would then sit outside DVC's view of the stage.
It leaves exactly one shape, which is the one above.

## Signing is a rebuild barrier

Signing is not deterministic.
A C2PA manifest carries a fresh instance ID and a timestamp, so signing
identical bytes twice produces two different files.
An unsigned Matplotlib figure, by contrast, is byte-identical across runs.

The consequence under DVC is specific.
Edit a comment in a plotting script: DVC reruns the stage, and *without*
signing the figure comes out byte-identical, so its hash is unchanged and
every downstream stage is skipped.
*With* signing, the same no-op edit produces a new signature, a new hash, and
the whole chain reruns—the composite re-embed, the export, the document build.

This only costs anything on changes that do not affect content, but those are
common: comments, formatting, a lock file bump that does not reach the output.

The remedy is to sign at the boundary rather than throughout.
Content Credentials earn their keep on artifacts that *leave the repository*—
the published figure, the file pasted into a manuscript.
An internal panel or a `.drawio` source never travels, so leaving it unsigned
costs nothing and stops the cascade.

That makes signing a per-stage decision rather than a project-wide switch,
which also reads correctly as a statement of intent: this is the artifact I am
putting my name on.

<!-- prettier-ignore -->
!!! note
    Leaving an intermediate panel unsigned does not lose the AI disclosure.
    Figmint reads each input's *own* Content Credentials when it builds the
    manifest, so an AI-generated panel that carries its generator's manifest
    still makes the composite `compositeWithTrainedAlgorithmicMedia`.
    What is lost is that panel's own ingredient entry, which is detail rather
    than disclosure.

## Where a fact lives

A Calkit `ImportedDataset` records where a dataset came from:

```yaml
datasets:
  - path: data/raw.csv
    title: Measurements
    imported_from:
      project: someone/their-project
      path: data/raw.csv
      git_rev: a1b2c3d
```

That is the same fact as Figmint's

```sh
figmint declare data/raw.csv \
    --calkit calkit.io/someone/their-project/data/raw.csv@a1b2c3d
```

Do not record it twice and reconcile them.
Two copies of one fact drift, and a sync step is a third thing that can be
wrong—silently, because nothing checks a synchronizer.
`calkit.yaml` is hand-authored and is the natural home for "this dataset came
from that project", so Figmint should read it as the origin declaration rather
than storing its own copy.

Where both exist and disagree, report the disagreement rather than picking a
winner.
This is the same rule Figmint already applies to Content Credentials: a file
that carries its own manifest is not restated in the record, and when the two
disagree the panel says so.

## Reading `dvc.lock`

Figmint can read `dvc.lock` for the *structure*—which command produced which
outputs from which inputs.
That removes any need for Figmint to discover the graph itself in a Calkit
project.

Figmint must not adopt its hashes.
DVC records `hash: md5`, which is entirely adequate for cache invalidation,
the only job it has there.
It is not adequate as evidence: MD5 has practical chosen-prefix collisions, so
a different figure with a matching MD5 is constructible rather than
theoretical.
The header at the top of `figmint.toml` tells agents that altering these
hashes is falsifying evidence, and that claim is only worth making about a
hash that cannot be forged.

So: structure from `dvc.lock`, SHA256 from Figmint.
`figmint.toml` then holds what only it has—content hashes, declarations, and
signature state—and can point at stage names instead of restating commands.

## What not to compile

It is tempting to compile the Figmint graph into `dvc.yaml`.
It does not work in general, for the reason the record exists: `figmint.toml`
is *observed*, written after the fact, and a build plan has to be known
before anything runs.
A first run would have nothing to compile from, and a stale record would
compile a stale plan.

The composite figure case is the real exception, and it is worth serving.
The panels a diagram embeds are readable *from the diagram* before running,
from the `src` attributes Figmint writes on each shape.
That is a query on a file, not a record of the past, so it belongs in a plan.

Without it, a stage has to list the panels by hand:

```yaml
embed-figure:
  deps:
    - figures/composite.drawio
    - figures/turbine.png
    - figures/cp_curve.png
```

Add a panel, forget to update this, and DVC will not rerun.
A stage kind that asks Figmint what the diagram embeds cannot drift, and it is
the one part of the graph Calkit cannot work out for itself.

## `figmint rebuild` and `calkit run`

These are the one genuine conflict: two components that each claim to know how
to rebuild the project, from different graphs.
They will drift.

In a Calkit project, `figmint rebuild` should defer.
`figmint status` still reports, but the repair it suggests becomes
`calkit run`.

## Overlap that is fine

Both record the environment lock file.
Figmint records its hash; Calkit records it as a DVC dependency.
Same file, different questions—"was this the environment?" versus "should I
rerun?"—and neither writes the other's copy, so there is nothing to drift.

Both hash every input and output, which costs some I/O on large files.
That is the price of keeping an evidence hash separate from a cache key.

## Packaging

Figmint stays a standalone package.
Calkit may depend on Figmint; Figmint must not depend on Calkit.
Today it shells out to `calkit describe env` when a command starts with a
Calkit prefix, and degrades when Calkit is absent, which is the right
direction for that dependency.

The cost of the split is real: two files a reader has to understand, and
version skew in the record format.
The benefit is that the evidence layer works for a plain uv project, a Quarto
document, or a LaTeX paper with no Calkit adoption at all, and that work on
Content Credentials and AI attestation is not tied to a pipeline runner's
release cycle.
