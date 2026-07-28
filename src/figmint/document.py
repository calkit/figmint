"""Loading and inspecting `.fig.yaml` documents from Python.

The editor is the primary author of these files, but agents and CI need to read
them too — that is the whole point of the format being text. This module is the
read side of that contract, kept deliberately close to the YAML rather than
wrapped in a deep object model, so a document with an unfamiliar node type
loads fine instead of exploding.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

DOCUMENT_SUFFIX = ".fig.yaml"
FORMAT_VERSION = "0.1"


class DocumentError(ValueError):
    """Raised when a file is not a usable figmint document."""


@dataclass
class Document:
    """A parsed `.fig.yaml`."""

    path: Path
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.data.get("id") or self.path.stem)

    @property
    def title(self) -> str | None:
        title = self.data.get("title")
        return str(title) if title else None

    @property
    def canvas(self) -> dict[str, Any]:
        return self.data.get("canvas") or {}

    def __post_init__(self) -> None:
        # Normalize once, in place, so `sources` and `nodes` alias the parsed
        # data rather than returning throwaway copies. A property that quietly
        # discards `document.nodes.append(...)` is a trap worth designing out.
        sources = self.data.get("sources")
        if not isinstance(sources, dict):
            sources = {}
        self.data["sources"] = {
            k: v for k, v in sources.items() if isinstance(v, dict)
        }

        nodes = self.data.get("nodes")
        if not isinstance(nodes, list):
            nodes = []
        self.data["nodes"] = [n for n in nodes if isinstance(n, dict)]

    @property
    def sources(self) -> dict[str, dict[str, Any]]:
        return self.data["sources"]

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return self.data["nodes"]

    def used_source_keys(self) -> list[str]:
        """Source keys referenced by at least one node, in document order."""
        keys: list[str] = []
        for node in self.nodes:
            if node.get("type") == "image":
                key = node.get("source")
                if isinstance(key, str) and key not in keys:
                    keys.append(key)
        return keys

    def source_path(self, key: str) -> Path | None:
        """Absolute path of a source, resolved relative to the document."""
        source = self.sources.get(key)
        if not source or not source.get("path"):
            return None
        return (self.path.parent / str(source["path"])).resolve()


def load(path: Path) -> Document:
    """Read and minimally validate a figmint document."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DocumentError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise DocumentError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise DocumentError(f"{path}: document must be a YAML mapping")
    if "figmint" not in raw:
        raise DocumentError(f"{path}: missing `figmint:` format version key")
    if "canvas" not in raw:
        raise DocumentError(f"{path}: missing `canvas:`")
    return Document(path=path, data=raw)


def discover(root: Path) -> Iterator[Path]:
    """Find figmint documents under a directory, skipping the usual noise."""
    skip = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist"}
    for path in sorted(root.rglob(f"*{DOCUMENT_SUFFIX}")):
        if any(part in skip for part in path.relative_to(root).parts):
            continue
        yield path
