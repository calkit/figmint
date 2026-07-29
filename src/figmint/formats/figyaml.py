"""`.fig.yaml` as a figure document.

figmint's own format, where components are *referenced* by path. This is a thin
wrapper over `figmint.document.Document`, which already reads the file; the
adapter's job is only to present it in the shared shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import document as document_mod
from .base import Canvas, Component, DocumentError, FigureDocument


class FigYamlDocument(FigureDocument):
    embeds_components = False

    def __init__(self, inner: document_mod.Document) -> None:
        self.inner = inner
        self.path = inner.path

    @classmethod
    def load(cls, path: Path) -> "FigYamlDocument":
        try:
            return cls(document_mod.load(path))
        except document_mod.DocumentError as exc:
            raise DocumentError(str(exc)) from exc

    @property
    def id(self) -> str:
        return self.inner.id

    @property
    def title(self) -> str | None:
        return self.inner.title

    @property
    def canvas(self) -> Canvas:
        canvas = self.inner.canvas
        return Canvas(
            width=float(canvas.get("width") or 0),
            height=float(canvas.get("height") or 0),
            units=str(canvas.get("units") or "pt"),
        )

    def components(self) -> list[Component]:
        out: list[Component] = []
        for key in self.inner.used_source_keys():
            source = self.inner.sources.get(key) or {}
            out.append(
                Component(
                    key=key,
                    origin=str(source.get("path") or key),
                    recorded_hash=source.get("hash"),
                    media_type=source.get("mediaType"),
                    provenance=dict(source.get("provenance") or {}),
                    credentials=source.get("credentials"),
                    embedded=None,  # referenced, not embedded
                    resolved=self.inner.source_path(key),
                )
            )
        return out

    def nodes(self) -> list[dict[str, Any]]:
        return self.inner.nodes
