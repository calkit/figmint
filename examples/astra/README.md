# figmint + ASTRA

**ASTRA declares the decision space** — every methodological choice that could
plausibly move a number, with its options and the reasoning behind each.
**figmint records what actually ran** — the SHA256 of every input as it was when
the output was made, who is answerable for the primary files, and the Content
Credentials on the figure that leaves the repository.

The join between them is the command. Every recipe in `astra.yaml` is wrapped in
`figmint run`, and every decision reaches the command line, so the command
figmint writes into `figmint.toml` names the options that produced the artifact
sitting beside it:

```toml
[artifact."results/peak.json"]
command = "uv run python src/peak.py --fit polynomial --estimator fit_peak"
```

"Which universe is this number from?" becomes a question about the record
rather than about somebody's memory.

## Prerequisites

Both tools wrap the work from the outside, so both belong on your `PATH` rather
than in the project environment — and `pyproject.toml` here mentions neither:

```sh
uv tool install astra-analysis
uv tool install figmint-fresh
```

## Running it

```sh
uv lock
uv run python src/run.py                          # the baseline universe
uv run python src/run.py universes/smoothed.yaml  # the alternative
figmint status
```

`src/run.py` is a ~60-line runner and is not part of either tool. `astra`
validates and inspects; it never executes recipes, because the choice of runner
is yours and the spec should not encode one. This is the smallest one that
substitutes the placeholders and runs the commands in dependency order.

The declarations are a one-time authoring step. Nothing produces the dataset or
the scripts, and a declaration is a statement by a person:

```sh
figmint declare data/performance.csv --mine
figmint declare src/curves.py --mine --with-ai 'Claude Opus 5'
# ...and the rest of src/
```

## The record is committed

`figmint.toml` is in version control, along with every artifact it describes.
That is not incidental to the example: the record _is_ the evidence, and a
project that ignores its own record is one where nobody can check anything
without rebuilding first. Clone this and `figmint status` has an answer
immediately.

## The two universes disagree, which is the point

```sh
uv run python src/run.py && cat results/peak.json
# peak C_P 0.4010 at λ = 2.80

uv run python src/run.py universes/smoothed.yaml && cat results/peak.json
# peak C_P 0.3691 at λ = 2.71
```

Same data, same code, two defensible readings, and an 8% difference in the
number a paper would quote. Neither is wrong. What would be wrong is for that
choice to live in a plotting call where no reader could find it — the silent
default, which ASTRA exists to prevent and which nothing else flags.

`peak_estimator: fit_peak` is marked `requires: [curve_fit.polynomial]`, because
taking the maximum of a curve you did not fit is not a coherent request.
`astra universe check` catches that pairing before anything runs.

## The gap this example found

figmint records the files a command **names**, not the ones a script imports.
`src/curves.py` holds the fit shared by the plots and the peak — precisely so a
figure and the number quoted from it cannot disagree — but it appears nowhere on
the command line, so figmint could not see it. Editing it changed every result
and made nothing stale.

The fix is one flag, and it is in the recipes:

```
figmint run --no-sign -i {inputs.measurements} -i src/curves.py ...
```

Worth knowing in general: figmint hashes what the command mentions, which
catches the entry-point script for free and misses everything it imports. If a
module can change a result, pass it with `-i`.

## Signing is a rebuild barrier

`--no-sign` on the two panels, and its absence on `performance.svg`.

A C2PA manifest carries a fresh instance ID and a timestamp, so signing
identical bytes twice produces two different files. Sign an intermediate and
every no-op edit produces a new hash and a rebuild of everything downstream.

So sign at the **boundary**: the artifact that leaves the repository. The panels
never travel; the stacked figure does. That also reads correctly as a statement
of intent — _this is the artifact I am putting my name on_.

`results/peak.json` is recorded but not signed, because JSON has nowhere to put
a manifest. For that output the line in `figmint.toml` is the only provenance
there is, which is exactly why the header at the top of that file is not
decoration.

## What each tool would miss alone

Without figmint, `astra.yaml` says which options _should_ have produced the
figure and cannot say which ones did, nor whether the file on disk is still the
one they produced.

Without ASTRA, `figmint.toml` records `--fit polynomial` faithfully and has
nothing to say about what else could have been chosen, why, or what it would
have cost. A record of one path through a decision space that is nowhere
written down.
