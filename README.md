# Figmint

Figmint provides artifact provenance tracking, including support for
composite artifacts like figure PNGs and publication PDFs,
which can be traced all
the way back to their primary inputs, e.g., scripts or images generated
with AI tools.

Figmint outputs are signed and contain Content Credentials metadata.

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
that creates lock files so those can be tracked as part of the provenance,
since they are important input information.
Practically this means that the command must start with one of the following:

- `uv run`
- `calkit xenv` (or `ck xenv`)
- `julia --project={some local path}`

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
