"""Declaring where an imported component came from.

The policy in `provenance.py` refuses anonymous files. This is the way to stop a
file being anonymous: record its origin in a sidecar next to it.

This is deliberately not a download tool. Fetching a URL at import time would
produce a file whose origin is recorded but whose retrieval is not repeatable —
the far better move, when the project has a pipeline, is a Calkit stage that
fetches the file as a declared output, which gets you `reproducible` instead of
`declared`. `figmint import` exists for the cases where that is overkill or
impossible: a micrograph off an instrument, a figure from a paper, a logo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

SIDECAR_SUFFIX = ".prov.yaml"


class ImportError_(ValueError):
    """Raised when an import cannot be recorded."""


@dataclass
class Origin:
    """What is being claimed about where a file came from."""

    imported_from: str | None = None
    doi: str | None = None
    url: str | None = None
    citation: str | None = None
    license: str | None = None
    note: str | None = None

    def is_empty(self) -> bool:
        return not any(
            (self.imported_from, self.doi, self.url, self.citation)
        )

    def to_sidecar(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.imported_from:
            data["importedFrom"] = self.imported_from
        if self.doi:
            data["doi"] = self.doi
        if self.url:
            data["url"] = self.url
        if self.citation:
            data["citation"] = self.citation
        if self.license:
            data["license"] = self.license
        if self.note:
            data["note"] = self.note
        return data


def sidecar_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + SIDECAR_SUFFIX)


def declare(target: Path, origin: Origin, *, overwrite: bool = False) -> Path:
    """Write (or update) the provenance sidecar for an imported component."""
    if not target.is_file():
        raise ImportError_(f"no such file: {target}")
    if origin.is_empty():
        raise ImportError_(
            "an import needs a stated origin — pass at least one of "
            "--from, --url, --doi, or --citation"
        )

    sidecar = sidecar_path(target)
    existing: dict[str, Any] = {}
    if sidecar.exists():
        if not overwrite:
            try:
                loaded = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
                existing = loaded if isinstance(loaded, dict) else {}
            except yaml.YAMLError as exc:
                raise ImportError_(f"{sidecar} is not valid YAML: {exc}") from exc

    data = {**existing, **origin.to_sidecar()}
    data["importedAt"] = datetime.now(timezone.utc).isoformat()

    header = (
        "# Provenance for an imported component, written by `figmint import`.\n"
        "# This file states where the artifact came from. Without it, figmint\n"
        "# treats the artifact as unidentified and (by default) refuses to place\n"
        "# it in a figure.\n"
    )
    sidecar.write_text(
        header + yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    return sidecar
