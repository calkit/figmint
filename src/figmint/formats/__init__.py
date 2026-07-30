"""Reading figure documents, whatever they are written in.

`status`, `check`, `accept`, `reimport`, and `build` only ever need two things
from a document:

1. Which components does it use, with what recorded provenance?
2. Where is each one placed, and how big is the canvas?

Everything else in figmint — hashing, C2PA credentials, the provenance ladder,
the Calkit lookup, staleness, signing — is built on those two answers and does
not care what file they came from. So they are the whole adapter surface.

Two formats implement it:

- `.fig.yaml`, figmint's own, where components are **referenced** by path.
- `.drawio`, where components must be **embedded** as data URIs because
  draw.io's sandbox refuses to load local files. A draw.io figure therefore
  carries copies of its components rather than pointers to them.

That difference is the reason the abstraction exists rather than being implied.
It shows up in exactly one place — `Component.content()`, which reads from disk
for one format and from the document itself for the other — and is invisible
everywhere else.
"""

from __future__ import annotations

from pathlib import Path

from . import base
from .base import Canvas, Component, DocumentError, FigureDocument, UnsupportedFormat
from .figyaml import FigYamlDocument
from .drawio import DrawioDocument

#: Extension suffix -> reader. Longest suffix wins, so `.fig.yaml` is matched
#: before a bare `.yaml` would be.
READERS: dict[str, type[FigureDocument]] = {
    ".fig.yaml": FigYamlDocument,
    ".drawio": DrawioDocument,
    ".drawio.xml": DrawioDocument,
    ".drawio.svg": DrawioDocument,
}


def supported_suffixes() -> list[str]:
    return sorted(READERS, key=len, reverse=True)


def reader_for(path: Path) -> type[FigureDocument] | None:
    name = path.name.lower()
    for suffix in supported_suffixes():
        if name.endswith(suffix):
            return READERS[suffix]
    return None


def open_document(path: Path) -> FigureDocument:
    """Open a figure document, picking the reader from its extension."""
    reader = reader_for(path)
    if reader is None:
        raise UnsupportedFormat(
            f"{path}: not a figure document "
            f"(expected one of {', '.join(supported_suffixes())})"
        )
    return reader.load(path)


def is_document(path: Path) -> bool:
    return reader_for(path) is not None


__all__ = [
    "base",
    "Canvas",
    "DocumentError",
    "Component",
    "DrawioDocument",
    "FigYamlDocument",
    "FigureDocument",
    "UnsupportedFormat",
    "is_document",
    "open_document",
    "reader_for",
    "supported_suffixes",
]
