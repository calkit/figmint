# fromwhere + Calkit

Two tools with different jobs, in one project. **Calkit owns execution and the
graph** — what to run, in what order, what can be skipped. **fromwhere owns the
evidence** — SHA256 hashes as an attestable record, declarations of authorship
and generative-AI involvement, and Content Credentials embedded in the outputs
that leave the repository.

See [`docs/calkit.md`](../../docs/calkit.md) for the reasoning. This is that
arrangement, running.

## Prerequisites

- [Calkit](https://github.com/calkit/calkit): `uv tool install calkit-python`
- fromwhere **on your `PATH` as a tool**, not in the project environment:
  `uv tool install fromwhere`

That second one is not incidental. fromwhere wraps each stage command from the
outside, so it cannot live in the environment the science runs in — and
`requirements.txt` here deliberately does not mention it. Calkit may depend on
fromwhere; fromwhere must not depend on Calkit.

## Running it

```sh
calkit run       # build whatever is out of date
fromwhere status   # is the evidence still true?
```

Both, and in that order. They answer different questions and neither replaces
the other.

The declarations are a one-time authoring step — nothing produces a dataset or
a plotting script, so without them the record has a hole at the bottom that
every check above it passes straight over:

```sh
fromwhere declare data/performance.csv --mine
fromwhere declare scripts/plot_cp.py --mine --with-ai 'Claude Opus 5'
fromwhere declare scripts/plot_ct.py --mine --with-ai 'Claude Opus 5'
fromwhere declare scripts/stack.py   --mine --with-ai 'Claude Opus 5'
```

## The record is committed

`provenance.toml` is in version control, along with every artifact it describes.
That is not incidental to the example: the record _is_ the evidence, and a
project that ignores its own record is one where nobody can check anything
without rebuilding first. Clone this and `fromwhere status` has an answer
immediately.

## The shape of a stage

```yaml
plot-cp:
  kind: command
  environment: main
  command: >-
    fromwhere run --no-sign
    -i data/performance.csv -o figures/cp_curve.svg
    -- calkit xenv -n main -- python scripts/plot_cp.py
```

fromwhere wraps the **stage command**, not Calkit. Wrapping Calkit would make
fromwhere the entrypoint, which is the thing this arrangement exists to avoid.

Two details that look redundant and are not:

`kind: command` rather than `python-script`, because a `python-script` stage
runs the script and nothing else — there is nowhere to put the wrapper.

The inner `calkit xenv -n main` survives even though Calkit already runs the
stage inside that environment. fromwhere refuses a bare command on purpose: the
environment is an input, and the prefix is how fromwhere knows which lock pins
it. It then asks `calkit describe env -n main` where that lock lives, because
Calkit fronts Docker, Conda, Julia and renv — each locking somewhere different
— and guessing would mean reimplementing Calkit's resolution and then drifting
from it.

## Why the wrapper has to be inside the stage

fromwhere signs an artifact _before_ hashing it, because embedding a C2PA
manifest changes the bytes. If DVC hashed an output and fromwhere signed it
afterward, the hash in `dvc.lock` would be wrong the moment signing finished,
DVC would see the output as modified, and the stage would rerun forever.

So signing happens inside the stage, before DVC ever looks at the file. That
rules out "run the pipeline, then sign", and it rules out fromwhere wrapping
Calkit. It leaves exactly one shape, which is the one above.

## Signing is a rebuild barrier

Look at `--no-sign` on the two panel stages, and its absence on `stack-panels`.
That is the most useful thing in this example.

A C2PA manifest carries a fresh instance ID and a timestamp, so signing
identical bytes twice produces two different files. Under DVC that has a
specific cost: edit a comment in `plot_cp.py` and, unsigned, the figure comes
out byte-identical, its hash is unchanged, and every downstream stage is
skipped. Signed, the same no-op edit produces a new signature, a new hash, and
the whole chain reruns.

So sign at the **boundary** rather than throughout. Content Credentials earn
their keep on artifacts that leave the repository — the published figure, the
file pasted into a manuscript. `cp_curve.svg` and `ct_curve.svg` never travel;
`performance.svg` does.

That makes signing a per-stage decision rather than a project-wide switch,
which also reads correctly as a statement of intent: _this is the artifact I am
putting my name on_.

Nothing is lost by leaving a panel unsigned. fromwhere reads each input's own
Content Credentials when it builds a manifest, so an AI-generated panel that
carries its generator's manifest still makes the composite
`compositeWithTrainedAlgorithmicMedia`. What is lost is that panel's own
ingredient entry, which is detail rather than disclosure.

## `dvc.lock` and `provenance.toml` side by side

They look alike — both record a command, its dependencies and its outputs, with
hashes, environment lock included. The difference is what the hashes are _for_,
and it is load bearing.

`dvc.lock` records `hash: md5`, which is entirely adequate for cache
invalidation, the only job it has. It is not adequate as evidence: MD5 has
practical chosen-prefix collisions, so a different figure with a matching MD5
is constructible rather than theoretical. The header at the top of
`provenance.toml` tells agents that altering those hashes is falsifying evidence,
and that claim is only worth making about a hash that cannot be forged.

Both hash every input and output, which costs some I/O. That is the price of
keeping an evidence hash separate from a cache key.

## Which tool repairs what

`calkit run` is the entrypoint. In a Calkit project, prefer it over
`fromwhere rebuild`: two components that each claim to know how to rebuild the
project, from different graphs, will drift. `fromwhere status` still reports —
that is the part Calkit has no answer for — but the repair is `calkit run`.

## What is not here yet

`docs/calkit.md` describes a `fromwhere: true` stage flag that would compile the
wrapper from the stage's declared inputs and outputs, so the `-i` and `-o`
flags are not written twice and cannot drift. Calkit does not implement that
yet, so this example writes them out. When it lands, the stages above collapse
to four lines each.

## Reproducibility gotchas

`calkit` and `fromwhere` are on your `PATH` as tools, not in `requirements.txt` —
they have to be, because both wrap the stage command from the outside. So the
environment the science runs in is pinned by `.calkit/env-locks/`, and the two
tools wrapping it are not.

That is the general shape of the limitation: fromwhere records the lock of the
environment the _command_ ran in, and whatever wraps that command is outside it.
See the note in the top-level README.
