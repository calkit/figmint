# Figmint

Figmint provides artifact provenance tracking, including support for
composite artifacts like figure PNGs and publication PDFs,
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

That records "created by A Researcher with Claude Opus 5" — who is
accountable and how the file was made, which are separate questions. Using
`--with-ai` without naming a person is refused.

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

### Freshness checking

To see if a given output's inputs (including environment lock files) have
changed, rendering it stale, use the `status` command.

```sh
figmint status <path>
```

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

To export an SVG containing the provenance information, run:

```sh
figmint drawio export my-diagram.drawio my-diagram.svg
```

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
