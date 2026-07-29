"""The document abstraction the provenance machinery is written against."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class UnsupportedFormat(ValueError):
    """Raised for a file figmint does not know how to read."""


class DocumentError(ValueError):
    """Raised when a file is the right kind but not usable."""


@dataclass
class Canvas:
    """Page size, always in points regardless of what the file stores.

    Normalising here rather than at each call site is most of the reason this
    layer exists: draw.io measures in hundredths of an inch, figmint's own
    format in points, and nothing downstream should have to know that.
    """

    width: float
    height: float
    units: str = "pt"


@dataclass
class Component:
    """One placed component, and enough to say where it came from."""

    #: Stable key within the document.
    key: str
    #: Path it came from, relative to the project. The thing to re-read when
    #: checking freshness, and to re-embed from on reimport.
    origin: str
    #: Hash recorded when the component was placed or embedded.
    recorded_hash: str | None = None
    media_type: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    credentials: dict[str, Any] | None = None
    #: Content carried inside the document, when the format embeds rather than
    #: references. `None` means "read it from `origin`".
    embedded: bytes | None = None
    #: Absolute path of `origin`, resolved against the document.
    resolved: Path | None = None

    @property
    def is_embedded(self) -> bool:
        return self.embedded is not None

    def content(self) -> bytes | None:
        """The bytes this document actually composes with.

        For an embedding format this is the copy inside the document, which may
        legitimately differ from what is on disk now — that difference is
        precisely what staleness reports.
        """
        if self.embedded is not None:
            return self.embedded
        if self.resolved and self.resolved.is_file():
            return self.resolved.read_bytes()
        return None

    def embedded_hash(self) -> str | None:
        """Hash of the embedded copy, for formats that carry one."""
        if self.embedded is None:
            return None
        return f"sha256:{hashlib.sha256(self.embedded).hexdigest()}"


class FigureDocument:
    """Base class for readable figure documents.

    Subclasses supply `load`, `components`, `canvas`, and — where the format can
    express it — `nodes` for the compositor. `save_component_hashes` is optional:
    a format that cannot be written back simply does not implement it.
    """

    #: Whether components live inside the document rather than beside it.
    embeds_components: bool = False

    path: Path

    @classmethod
    def load(cls, path: Path) -> "FigureDocument":  # pragma: no cover - abstract
        raise NotImplementedError

    @property
    def id(self) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    @property
    def title(self) -> str | None:
        return None

    @property
    def canvas(self) -> Canvas:  # pragma: no cover - abstract
        raise NotImplementedError

    def components(self) -> list[Component]:  # pragma: no cover - abstract
        raise NotImplementedError

    def nodes(self) -> list[dict[str, Any]]:
        """Drawable nodes in points, for the compositor.

        Formats that cannot be composed by figmint return an empty list; the
        caller falls back to the format's own exporter.
        """
        return []

    def component_origin(self, component: Component) -> Path | None:
        return component.resolved
