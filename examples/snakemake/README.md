# figmint + Snakemake

**Snakemake owns execution and the graph** — what is out of date, what runs, in
what order. **figmint owns the evidence** — the SHA256 of every input as it was
when the output was made, who is answerable for the primary files, and the
Content Credentials embedded in the artifact that leaves the repository.

## Prerequisites

Both wrap the work from the outside, so both live on your `PATH` rather than in
the project environment — and `pyproject.toml` here deliberately mentions
neither:

```sh
uv tool install snakemake
uv tool install figmint-fresh
```

## Running it

```sh
uv lock          # the environment figmint records as an input
snakemake --cores 1
figmint status
```

The declarations are a one-time authoring step. Nothing produces a dataset or a
plotting script, so without them the record has a hole at the bottom that every
check above it passes straight over — and a declaration is a statement by a
person, which is not something a workflow manager can make on their behalf:

```sh
figmint declare data/performance.csv --mine
figmint declare scripts/plot_cp.py --mine --with-ai 'Claude Opus 5'
figmint declare scripts/plot_ct.py --mine --with-ai 'Claude Opus 5'
figmint declare scripts/stack.py   --mine --with-ai 'Claude Opus 5'
```

## The record is committed

`figmint.toml` is in version control, along with every artifact it describes.
That is not incidental to the example: the record _is_ the evidence, and a
project that ignores its own record is one where nobody can check anything
without rebuilding first. Clone this and `figmint status` has an answer
immediately.

## The shape of a rule

```python
rule plot:
    input:
        data="data/performance.csv",
        script="scripts/plot_{panel}.py",
        lock="uv.lock",
    output:
        "figures/{panel}_curve.svg",
    shell:
        "figmint run --no-sign -i {input.data} -o {output}"
        " -- uv run python {input.script}"
```

figmint wraps the **shell command**, not Snakemake. Wrapping Snakemake would
make figmint the entrypoint, record one enormous artifact, and lose the
per-figure chain that is the entire point.

The command inside has to go through an environment manager with a lock file —
here `uv run`, so `uv.lock` is recorded alongside the CSV. figmint refuses a
bare command, and the refusal is deliberate: the same script under a different
Plotly draws a different figure, and a record naming only the data would call
that unchanged. `uv.lock` is listed as a rule input too, so Snakemake reruns on
a dependency bump for the same reason.

## Do they not both do the same thing?

Less than it looks. Snakemake keeps its own record of what it built and from
what, under `.snakemake/`, and it is good at it — a touched file whose content
did not change does _not_ trigger a rerun, and restoring an old version quiets
it again. Both tools notice a real edit; both go quiet when it is undone.

The difference is what the record is _for_:

```sh
rm -rf .snakemake/     # Snakemake now wants to rebuild all three rules
figmint status         # unchanged: everything still ok
```

Snakemake's state is a **cache** — local, disposable, about scheduling. Delete
it and you rebuild. figmint's is **evidence** — committed, human-readable, and
the thing the header at the top of it warns agents not to edit. Delete that and
you have not lost a cache.

And it is silent on everything figmint exists for. `.snakemake/` cannot say who
wrote a script, whether a model was involved, or whether the published figure
carries a manifest a reader outside the repository could check.

## Signing is a rebuild barrier

Look at `--no-sign` on the panel rule, and its absence on `stack`.

A C2PA manifest carries a fresh instance ID and a timestamp, so signing
identical bytes twice produces two different files. Sign an intermediate and
every no-op edit — a comment, a reformat — produces a new signature, a new
hash, and a rebuild of everything downstream.

So sign at the **boundary**. Content Credentials earn their keep on artifacts
that leave the repository: the published figure, the file pasted into a
manuscript. `cp_curve.svg` and `ct_curve.svg` never travel; `performance.svg`
does. That makes signing a per-rule decision rather than a project-wide switch,
which also reads correctly as a statement of intent: _this is the artifact I am
putting my name on_.

## Which tool repairs what

`snakemake` is the entrypoint. Prefer it over `figmint rebuild` here: two
components that each claim to know how to rebuild the project, from different
graphs, will drift. `figmint status` still reports — that is the part Snakemake
has no answer for — but the repair is `snakemake --cores 1`.

## Reproducibility gotchas

`snakemake` and `figmint` are on your `PATH` as tools, not in `pyproject.toml` —
they have to be, because both wrap the shell command from the outside. So the
environment the science runs in is pinned by `uv.lock`, and the two tools
wrapping it are not.

That is the general shape of the limitation: figmint records the lock of the
environment the _command_ ran in, and whatever wraps that command is outside it.
See the note in the top-level README.
