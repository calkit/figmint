"""Finding whichever pipeline a project actually uses.

figmint asks one question of a pipeline: *which stage declares this file as an
output?* Calkit answers it. So does Snakemake. So, in principle, do Make, Nextflow,
and DVC on its own. None of that is figmint's to define — it only needs to read
the declaration well enough to say the claim is checkable.

Routing through here rather than importing `calkit` directly is what keeps the
provenance ladder honest across projects. Rating a Snakemake analysis's figures
`unidentified` while an AI-generated image scored `signed` was not a measurement
of that project's rigour; it was a measurement of which file formats figmint
could parse, wearing the costume of a measurement of rigour.

Calkit is tried first because it carries more: environments, and a `dvc.lock`
that makes staleness checkable rather than unknown. A project with both gets the
stronger reading.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from . import calkit as calkit_mod
from . import snakemake as snakemake_mod


@runtime_checkable
class Pipeline(Protocol):
    """What figmint needs from a pipeline definition."""

    root: Path

    def stage_for(self, path: Path) -> Any | None: ...
    def unaccounted_inputs(self, stage: str) -> tuple[str, ...]: ...
    def stages_consuming(self, path: str) -> tuple[str, ...]: ...


#: Tried in order. Calkit first: it is the only one here that can also answer
#: whether a stage is *current*, so where both are present it says more.
READERS = (calkit_mod, snakemake_mod)


def find_project(start: Path) -> Path | None:
    """The nearest project root of any recognised kind."""
    for reader in READERS:
        found = reader.find_project(start)
        if found is not None:
            return found
    return None


def project_for(path: Path) -> Any | None:
    """The pipeline containing `path`, whichever kind it is."""
    for reader in READERS:
        project = reader.project_for(path)
        if project is not None:
            return project
    return None
