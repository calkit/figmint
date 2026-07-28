"""Scanning, hashing, and measuring the local figure directory.

Provenance starts here: every artifact we hand to the editor carries a content
hash, so the editor can tell later whether the file backing a placed panel has
changed since it was placed.
"""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import credentials as credentials_mod

#: Extensions we will offer as figure panels.
FIGURE_SUFFIXES = {
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".pdf",
}

MEDIA_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}

#: Directories never worth walking into.
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".dvc",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
}

#: CSS absolute length units, expressed in points.
_UNIT_TO_PT = {
    "": 0.75,  # bare numbers in SVG are px; 1px = 0.75pt
    "px": 0.75,
    "pt": 1.0,
    "pc": 12.0,
    "mm": 72.0 / 25.4,
    "cm": 72.0 / 2.54,
    "in": 72.0,
}


@dataclass
class Asset:
    """A figure file on disk, as presented to the editor."""

    path: str
    name: str
    hash: str
    size: int
    modified: str
    mediaType: str  # noqa: N815 - matches the TypeScript model
    intrinsic: dict[str, float] | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    #: Signed C2PA Content Credentials, when the producing tool emitted them.
    credentials: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data["provenance"]:
            data.pop("provenance")
        if data["intrinsic"] is None:
            data.pop("intrinsic")
        if data["credentials"] is None:
            data.pop("credentials")
        return data


def hash_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """Content hash of a file, prefixed with its algorithm.

    Prefixed so that the format can move to a different algorithm later without
    ambiguity in documents already written.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _length_to_pt(value: str) -> float | None:
    match = re.fullmatch(r"\s*([0-9.]+)\s*([a-z%]*)\s*", value, re.IGNORECASE)
    if not match:
        return None
    number, unit = match.groups()
    factor = _UNIT_TO_PT.get(unit.lower())
    if factor is None:  # percentages and em/ex are not resolvable standalone
        return None
    try:
        return float(number) * factor
    except ValueError:
        return None


def _svg_size(path: Path) -> tuple[float, float] | None:
    """Read intrinsic size from an SVG root element.

    Prefers explicit width/height; falls back to the viewBox, which is treated as
    user units (px). Only the first 8KB is read — the root element is always near
    the top, and figure SVGs can be very large.
    """
    try:
        head = path.read_bytes()[:8192].decode("utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"<svg\b[^>]*>", head, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    tag = match.group(0)

    width_attr = re.search(r'\bwidth\s*=\s*"([^"]+)"', tag)
    height_attr = re.search(r'\bheight\s*=\s*"([^"]+)"', tag)
    if width_attr and height_attr:
        width = _length_to_pt(width_attr.group(1))
        height = _length_to_pt(height_attr.group(1))
        if width and height:
            return width, height

    viewbox = re.search(r'\bviewBox\s*=\s*"([^"]+)"', tag)
    if viewbox:
        parts = re.split(r"[\s,]+", viewbox.group(1).strip())
        if len(parts) == 4:
            try:
                return float(parts[2]) * 0.75, float(parts[3]) * 0.75
            except ValueError:
                return None
    return None


def _png_size(path: Path) -> tuple[float, float] | None:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", header[16:24])
    return width * 0.75, height * 0.75


def _jpeg_size(path: Path) -> tuple[float, float] | None:
    """Walk JPEG segment markers to the start-of-frame, which holds the size."""
    with path.open("rb") as handle:
        if handle.read(2) != b"\xff\xd8":
            return None
        while True:
            marker = handle.read(2)
            if len(marker) < 2 or marker[0] != 0xFF:
                return None
            code = marker[1]
            length_bytes = handle.read(2)
            if len(length_bytes) < 2:
                return None
            (length,) = struct.unpack(">H", length_bytes)
            # SOF0..SOF15, excluding the non-frame markers DHT/JPG/DAC.
            if 0xC0 <= code <= 0xCF and code not in (0xC4, 0xC8, 0xCC):
                payload = handle.read(5)
                if len(payload) < 5:
                    return None
                height, width = struct.unpack(">HH", payload[1:5])
                return width * 0.75, height * 0.75
            handle.seek(length - 2, 1)


def intrinsic_size(path: Path) -> dict[str, float] | None:
    """Natural size of an image in points, or None when it can't be determined.

    Used only to seed a sensible aspect ratio on insert, so failing to read a
    format is not an error — the editor falls back to a square.
    """
    suffix = path.suffix.lower()
    try:
        if suffix == ".svg":
            size = _svg_size(path)
        elif suffix == ".png":
            size = _png_size(path)
        elif suffix in (".jpg", ".jpeg"):
            size = _jpeg_size(path)
        else:
            size = None
    except (OSError, struct.error):
        return None
    if size is None:
        return None
    width, height = size
    return {"width": round(width, 2), "height": round(height, 2)}


def describe(path: Path, root: Path) -> Asset:
    """Build an :class:`Asset` for one file."""
    stat = path.stat()
    credentials = credentials_mod.read(path)
    return Asset(
        path=path.relative_to(root).as_posix(),
        name=path.name,
        hash=hash_file(path),
        size=stat.st_size,
        modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        mediaType=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        intrinsic=intrinsic_size(path),
        provenance=merge_provenance(path, root, credentials),
        credentials=credentials.to_dict() if credentials else None,
    )


def merge_provenance(
    path: Path,
    root: Path,
    credentials: credentials_mod.ContentCredentials | None,
) -> dict[str, Any]:
    """Combine signed Content Credentials with the unsigned sidecar.

    Credentials win where the two disagree: they are cryptographically bound to
    the file's contents, whereas a sidecar is just a text file sitting next to
    it that anything could have written. The sidecar still supplies what C2PA
    has no field for — the script path, the command line, the upstream data
    files — so the two are complementary rather than redundant.
    """
    provenance = sidecar_provenance(path, root)
    if credentials is None:
        return provenance
    # `softwareAgent` is the tool named in the signed creation action, which is
    # a stronger claim than the sidecar's `generatedBy`.
    if credentials.softwareAgent:
        provenance["signedAgent"] = credentials.softwareAgent
    elif credentials.claimGenerator:
        provenance["signedAgent"] = credentials.claimGenerator
    return provenance


def sidecar_provenance(path: Path, root: Path) -> dict[str, Any]:
    """Read provenance recorded alongside an artifact.

    Looks for `<name>.prov.yaml` next to the file. This is the fallback for
    tools that do not sign their output — Calkit, DVC, a plain Makefile — and
    the place to record things C2PA has no field for, like the exact command
    line and the upstream data files.
    """
    sidecar = path.with_suffix(path.suffix + ".prov.yaml")
    if not sidecar.exists():
        return {}
    try:
        import yaml  # imported lazily; only needed when sidecars are in use

        data = yaml.safe_load(sidecar.read_text()) or {}
    except Exception:  # noqa: BLE001 - a bad sidecar must not break the scan
        return {}
    if not isinstance(data, dict):
        return {}
    allowed = {"generatedBy", "command", "commit", "derivedFrom"}
    return {k: v for k, v in data.items() if k in allowed}


def scan(root: Path, subdir: str | None = None) -> list[Asset]:
    """Walk the project for figure files, newest first."""
    base = root if subdir is None else safe_join(root, subdir)
    if not base.exists():
        return []

    assets: list[Asset] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in FIGURE_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        assets.append(describe(path, root))

    assets.sort(key=lambda a: a.modified, reverse=True)
    return assets


class UnsafePathError(ValueError):
    """Raised when a request tries to escape the project root."""


def safe_join(root: Path, relative: str) -> Path:
    """Resolve `relative` inside `root`, refusing anything that escapes it.

    The server hands out file contents by path, so this is the only thing
    standing between a URL and the rest of the filesystem. Resolve first, compare
    after — checking for `..` textually is not enough once symlinks exist.
    """
    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise UnsafePathError(f"path escapes project root: {relative}")
    return candidate
