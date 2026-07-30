"""`.drawio` as a figure document.

draw.io's Electron sandbox refuses to load local files during export, so an
imported component is **embedded** in the document as a base64 data URI. That has
one consequence worth stating plainly: importing an SVG through draw.io's UI
destroys every trace of where it came from. What lands in the file is an
anonymous blob.

figmint recovers that in two ways, in order:

1. **Declared attributes.** draw.io lets any shape carry arbitrary key/value
   pairs (Edit Data, Cmd+M), wrapped as `<object figmint.path="…">`. When those
   are present they are authoritative, and they survive round-trips through the
   app — verified against the CLI's own XML export.

2. **Content-hash matching.** With no attributes, the embedded bytes are hashed
   and compared against files in the project. An exact match identifies the
   origin without anyone having recorded it, which is what makes `figmint adopt`
   able to label a diagram somebody already drew.

Anything neither declares nor matches is genuinely unidentified, and reported as
such rather than quietly accepted.

Units: draw.io measures in hundredths of an inch, so a coordinate times 0.72 is
points. Confirmed empirically — a page authored at 650x292 exports to a PDF with
`MediaBox [0 0 468 210]`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..assets import FIGURE_SUFFIXES, MEDIA_TYPES, SKIP_DIRS
from .base import Canvas, Component, DocumentError, FigureDocument

logger = logging.getLogger(__name__)

#: draw.io units are 1/100 inch; points are 1/72 inch.
UNITS_TO_POINTS = 72.0 / 100.0

#: Provenance keys, written as plain shape attributes.
#:
#: Deliberately unprefixed. A `figmint.`-style prefix would namespace these away
#: from a user's own data, but draw.io strips the dotted prefix when it re-saves
#: a file — observed directly: `figmint.path` came back as `path`. So a prefix
#: buys nothing and silently changes the keys under us. Plain names it is, at the
#: cost of a small collision risk with someone's own `hash` or `src` field.
ATTR_SRC = "src"
ATTR_HASH = "hash"

#: Keys draw.io uses for its own purposes, never read as provenance.
RESERVED_ATTRS = frozenset(
    {"label", "placeholders", "tooltip", "link", "id", "style", "parent", "vertex", "edge"}
)

#: Provenance keys figmint understands. Anything else on the shape is left alone
#: but also carried through, so a user's own annotations are not lost.
PROVENANCE_ATTRS = frozenset(
    {
        ATTR_SRC,
        ATTR_HASH,
        "doi",
        "url",
        "citation",
        "license",
        "importedFrom",
        "generatedBy",
        "stage",
        "note",
    }
)

#: Legacy dotted form, still read so older files keep working.
LEGACY_PREFIX = "figmint."

_DATA_URI = re.compile(r"image=(data:[^;,]+(?:;base64)?,[^;\"]*)")


def to_points(value: float) -> float:
    return value * UNITS_TO_POINTS


def from_points(value: float) -> float:
    return value / UNITS_TO_POINTS


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def decode_diagram(node: ET.Element) -> ET.Element:
    """Get the `<mxGraphModel>` out of a `<diagram>`, compressed or not.

    draw.io desktop writes plain XML, but the web app and older files store the
    model as base64 + raw-deflate of URL-encoded XML. Both are common enough in
    the wild that reading only one would be a bug people hit immediately.
    """
    model = node.find("mxGraphModel")
    if model is not None:
        return model

    text = (node.text or "").strip()
    if not text:
        raise DocumentError("diagram is empty")

    try:
        raw = base64.b64decode(text)
    except (binascii.Error, ValueError) as exc:
        raise DocumentError(f"diagram is neither XML nor base64: {exc}") from exc

    try:
        # Raw deflate, no zlib header.
        inflated = zlib.decompress(raw, -15)
    except zlib.error as exc:
        raise DocumentError(f"could not inflate diagram: {exc}") from exc

    try:
        xml_text = urllib.parse.unquote(inflated.decode("utf-8"))
        return ET.fromstring(xml_text)
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise DocumentError(f"inflated diagram is not valid XML: {exc}") from exc


def decode_data_uri(uri: str) -> bytes | None:
    """Bytes from a data URI, base64 or percent-encoded."""
    header, _, payload = uri.partition(",")
    if not payload:
        return None
    if "base64" in header:
        try:
            return base64.b64decode(payload)
        except (binascii.Error, ValueError):
            return None
    try:
        return urllib.parse.unquote(payload).encode("utf-8")
    except UnicodeDecodeError:
        return None
    # draw.io commonly writes `data:image/svg+xml,<base64>` with no `;base64`
    # marker, so a bare-payload fallback is attempted by the caller.


#: Prefixes identifying an image that ships with draw.io rather than one the
#: author brought to the diagram.
BUNDLED_PREFIXES = ("img/", "stencils/", "shapes/")
EXTERNAL_SCHEMES = ("http://", "https://")


def _provenance_attrs(attrib: dict[str, str]) -> dict[str, str]:
    """Provenance keys from a shape's attributes, plain or legacy-dotted."""
    out: dict[str, str] = {}
    for key, value in attrib.items():
        if key.startswith(LEGACY_PREFIX):
            out[key[len(LEGACY_PREFIX) :]] = value
        elif key in PROVENANCE_ATTRS and key not in RESERVED_ATTRS:
            out[key] = value
    return out


def _image_from_style(style: str) -> str | None:
    """The image a shape carries, or None if it is not a figure component.

    draw.io's stock shapes reference bundled clipart (`image=img/clipart/…`), and
    counting those as components would fail every diagram that uses a gear or a
    cloud — which trains people to ignore provenance warnings. A component is an
    image the author brought in: an embedded data URI, or a path that could
    plausibly resolve inside the project.
    """
    match = _DATA_URI.search(style)
    if match:
        return match.group(1)

    plain = re.search(r"image=([^;\"]+)", style)
    if plain is None:
        return None
    value = plain.group(1)
    if value.startswith(BUNDLED_PREFIXES) or value.startswith(EXTERNAL_SCHEMES):
        return None
    return value


def _payload_bytes(uri: str) -> bytes | None:
    """Decode a data URI, tolerating draw.io's missing `;base64` marker."""
    decoded = decode_data_uri(uri)
    if decoded is not None and decoded[:1] not in (b"", None):
        # A percent-decoded payload that is really base64 will not start with
        # `<` for SVG or the PNG magic; try base64 as well and prefer whichever
        # yields something plausible.
        if decoded.lstrip()[:1] in (b"<",) or decoded[:4] == b"\x89PNG":
            return decoded
    payload = uri.partition(",")[2]
    try:
        candidate = base64.b64decode(payload)
    except (binascii.Error, ValueError):
        return decoded
    return candidate or decoded


# ---------------------------------------------------------------------------
# Project index for hash matching
# ---------------------------------------------------------------------------


def index_project(root: Path) -> dict[str, str]:
    """Map content hash -> project-relative path, for identifying blobs.

    Built once per document read. On a project with thousands of figures this is
    the expensive part of reading a `.drawio`, but it is the only way to give a
    name to something draw.io recorded anonymously.
    """
    index: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in FIGURE_SUFFIXES:
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        index.setdefault(f"sha256:{digest}", relative.as_posix())
    return index


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------


@dataclass
class Cell:
    """A shape carrying an image, with whatever figmint knows about it."""

    cell_id: str
    #: The `<object>` wrapper, when the shape has one.
    wrapper: ET.Element | None
    element: ET.Element
    image_uri: str | None
    geometry: dict[str, float]
    attributes: dict[str, str]


class DrawioDocument(FigureDocument):
    embeds_components = True

    #: True for `.drawio.svg`, where the diagram sits inside a rendered picture.
    rendered = False

    def __init__(self, path: Path, tree: ET.ElementTree, model: ET.Element) -> None:
        self.path = path
        #: Where this was loaded from, so an in-place write can be recognised
        #: even after `path` is repointed for an `-o` handoff.
        self._source_path = path
        self.tree = tree
        self.model = model
        self._project_root = self._find_root(path)
        self._index: dict[str, str] | None = None

    # --- loading ----------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "DrawioDocument":
        try:
            tree = ET.parse(path)
        except (OSError, ET.ParseError) as exc:
            raise DocumentError(f"{path}: not readable as XML ({exc})") from exc

        root = tree.getroot()

        # `.drawio.svg`: a rendered SVG that also carries the whole diagram in a
        # `content` attribute on the root element. One file that is both a
        # picture anything can display and an editable draw.io source — which is
        # exactly what makes it worth publishing.
        if root.tag.endswith("svg"):
            embedded = root.get("content")
            if not embedded:
                raise DocumentError(
                    f"{path}: an SVG with no embedded diagram "
                    f"(export from draw.io with --embed-diagram)"
                )
            try:
                inner = ET.fromstring(embedded)
            except ET.ParseError as exc:
                raise DocumentError(
                    f"{path}: embedded diagram is not valid XML ({exc})"
                ) from exc
            diagram = inner.find("diagram") if inner.tag == "mxfile" else None
            model = (
                decode_diagram(diagram)
                if diagram is not None
                else (inner if inner.tag == "mxGraphModel" else None)
            )
            if model is None:
                raise DocumentError(f"{path}: embedded diagram has no model")
            document = cls(path, tree, model)
            # The rendered body cannot be regenerated without draw.io, so this
            # form is read-only. See `save`.
            document.rendered = True
            return document

        diagram = root.find("diagram") if root.tag == "mxfile" else None
        if diagram is None:
            # A bare `<mxGraphModel>` document is also valid.
            if root.tag == "mxGraphModel":
                return cls(path, tree, root)
            raise DocumentError(f"{path}: no <diagram> found")
        return cls(path, tree, decode_diagram(diagram))

    @staticmethod
    def _find_root(path: Path) -> Path:
        """Project root, for resolving and matching component paths."""
        for directory in [path.parent, *path.parent.parents]:
            if (directory / ".git").exists() or (directory / "calkit.yaml").exists():
                return directory
        return path.parent

    @property
    def project_root(self) -> Path:
        return self._project_root

    def project_index(self) -> dict[str, str]:
        if self._index is None:
            self._index = index_project(self._project_root)
        return self._index

    # --- metadata ---------------------------------------------------------

    @property
    def id(self) -> str:
        return self.path.name.removesuffix(".xml").removesuffix(".drawio")

    @property
    def title(self) -> str | None:
        root = self.tree.getroot()
        diagram = root.find("diagram") if root.tag == "mxfile" else None
        name = diagram.get("name") if diagram is not None else None
        return name or None

    @property
    def canvas(self) -> Canvas:
        width = float(self.model.get("pageWidth") or 0)
        height = float(self.model.get("pageHeight") or 0)
        return Canvas(
            width=to_points(width), height=to_points(height), units="pt"
        )

    # --- cells ------------------------------------------------------------

    def cells(self) -> list[Cell]:
        """Shapes carrying an image, in document order."""
        root = self.model.find("root")
        if root is None:
            return []

        out: list[Cell] = []
        for child in root:
            wrapper: ET.Element | None = None
            element = child
            attributes: dict[str, str] = {}

            if child.tag in ("object", "UserObject"):
                wrapper = child
                attributes = _provenance_attrs(child.attrib)
                inner = child.find("mxCell")
                # draw.io writes both shapes: `<object><mxCell style=…>` and an
                # `<object style=…>` carrying the style itself. Requiring the
                # nested cell silently skipped the second form, which meant a
                # diagram full of images reported *zero* components and passed
                # every check — the worst way for this to fail.
                element = inner if inner is not None else child

            style = element.get("style") or ""
            uri = _image_from_style(style)
            if uri is None:
                continue

            geometry_node = element.find("mxGeometry")
            geometry = {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
            if geometry_node is not None:
                for key in geometry:
                    try:
                        geometry[key] = float(geometry_node.get(key) or 0)
                    except ValueError:
                        geometry[key] = 0.0

            cell_id = (
                (wrapper.get("id") if wrapper is not None else None)
                or element.get("id")
                or f"cell-{len(out)}"
            )
            out.append(
                Cell(
                    cell_id=cell_id,
                    wrapper=wrapper,
                    element=element,
                    image_uri=uri,
                    geometry=geometry,
                    attributes=attributes,
                )
            )
        return out

    def components(self) -> list[Component]:
        components: list[Component] = []
        for cell in self.cells():
            uri = cell.image_uri or ""
            embedded = _payload_bytes(uri) if uri.startswith("data:") else None

            declared_path = cell.attributes.get(ATTR_SRC) or cell.attributes.get(
                "path"
            )
            declared_hash = cell.attributes.get(ATTR_HASH)
            key = cell.cell_id

            origin = declared_path
            if origin is None and not uri.startswith("data:"):
                # A plain (non-embedded) reference. draw.io cannot export these,
                # but a hand-written file may well contain one.
                origin = uri
            if origin is None and embedded is not None:
                # Nothing declared: try to recognise the bytes.
                digest = f"sha256:{hashlib.sha256(embedded).hexdigest()}"
                origin = self.project_index().get(digest)

            media_type = None
            if origin:
                media_type = MEDIA_TYPES.get(Path(origin).suffix.lower())
            if media_type is None and uri.startswith("data:"):
                media_type = uri.partition(",")[0][5:].split(";")[0] or None

            components.append(
                Component(
                    key=key,
                    origin=origin or "",
                    # With nothing declared, the embedded copy is the only
                    # version we know of, so its own hash is what was placed.
                    recorded_hash=declared_hash
                    or (
                        f"sha256:{hashlib.sha256(embedded).hexdigest()}"
                        if embedded is not None
                        else None
                    ),
                    media_type=media_type,
                    provenance={
                        k: v
                        for k, v in cell.attributes.items()
                        if k not in (ATTR_SRC, ATTR_HASH, "path")
                    },
                    credentials=None,
                    embedded=embedded,
                    resolved=(self._project_root / origin) if origin else None,
                )
            )
        return components

    def nodes(self) -> list[dict[str, Any]]:
        """Image placements in points, so the compositor can build from them."""
        out: list[dict[str, Any]] = []
        for cell, component in zip(self.cells(), self.components()):
            out.append(
                {
                    "id": cell.cell_id,
                    "type": "image",
                    "source": component.key,
                    "x": to_points(cell.geometry["x"]),
                    "y": to_points(cell.geometry["y"]),
                    "width": to_points(cell.geometry["width"]),
                    "height": to_points(cell.geometry["height"]),
                    "fit": "contain",
                }
            )
        return out

    # --- writing ----------------------------------------------------------

    def annotate(self, assignments: dict[str, dict[str, str]]) -> int:
        """Write `figmint.*` attributes onto shapes, in place.

        A plain `<mxCell>` has nowhere to put metadata, so it is wrapped in an
        `<object>` — the same structure draw.io's own Edit Data produces, which
        is why these attributes survive being opened and re-saved in the app.
        """
        root = self.model.find("root")
        if root is None:
            return 0

        written = 0
        for index, child in enumerate(list(root)):
            wrapper: ET.Element | None = None
            element = child
            if child.tag in ("object", "UserObject"):
                wrapper = child
                inner = child.find("mxCell")
                element = inner if inner is not None else child

            style = element.get("style") or ""
            if _image_from_style(style) is None:
                continue

            cell_id = (
                (wrapper.get("id") if wrapper is not None else None)
                or element.get("id")
                or ""
            )
            attributes = assignments.get(cell_id)
            if not attributes:
                continue

            if wrapper is element:
                # An <object> carrying the style itself, with no nested cell.
                # draw.io writes this form but then fails to render it — an
                # empty canvas and a failed export. Since we are rewriting the
                # shape anyway, normalise it to the nested form rather than
                # preserving something broken.
                cell = ET.SubElement(wrapper, "mxCell")
                for name in ("style", "parent", "vertex", "edge"):
                    if name in wrapper.attrib:
                        cell.set(name, wrapper.attrib.pop(name))
                # `value` belongs on the wrapper as `label`; leaving it on the
                # cell makes draw.io collapse the wrapper and lose the data.
                if "value" in wrapper.attrib:
                    wrapper.set("label", wrapper.attrib.pop("value"))
                for sub in [c for c in wrapper if c.tag != "mxCell"]:
                    wrapper.remove(sub)
                    cell.append(sub)
                for name, value in attributes.items():
                    wrapper.set(name, value)
                written += 1
                continue

            if wrapper is None:
                wrapper = ET.Element("object")
                wrapper.set("label", element.get("value") or "")
                wrapper.set("id", cell_id)
                # The id moves to the wrapper; leaving it on both makes draw.io
                # treat them as two cells.
                element.attrib.pop("id", None)
                # And `value` must move too, not just be copied. A nested cell
                # that still carries `value` makes draw.io collapse the wrapper
                # on save and discard every custom attribute with it — verified
                # by round-tripping through draw.io's own CLI.
                element.attrib.pop("value", None)
                root.remove(element)
                wrapper.append(element)
                root.insert(index, wrapper)

            for name, value in attributes.items():
                wrapper.set(name, value)
            written += 1

        return written

    @classmethod
    def create(cls, path: Path, page: str = "Page-1") -> "DrawioDocument":
        """An empty diagram, for placing components into."""
        text = (
            '<mxfile host="figmint">'
            f'<diagram id="{path.stem}" name="{page}">'
            '<mxGraphModel dx="850" dy="1100" grid="1" gridSize="10" guides="1" '
            'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            'pageWidth="850" pageHeight="1100" math="1" shadow="0">'
            '<root><mxCell id="0"/><mxCell id="1" parent="0"/></root>'
            "</mxGraphModel></diagram></mxfile>"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return cls.load(path)

    def _write_diagram(self, target: Path) -> None:
        """Write the diagram as a standalone `.drawio`, dropping the rendering."""
        mxfile = ET.Element("mxfile", {"host": "figmint"})
        diagram = ET.SubElement(
            mxfile, "diagram", {"id": target.stem, "name": self.title or "Page-1"}
        )
        diagram.append(self.model)
        target.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(mxfile).write(
            target, encoding="utf-8", xml_declaration=False
        )

    def next_free_id(self) -> str:
        """An id not already used by the diagram."""
        root = self.model.find("root")
        used = set()
        for child in list(root) if root is not None else []:
            used.add(child.get("id"))
            for sub in child:
                used.add(sub.get("id"))
        n = 2
        while str(n) in used:
            n += 1
        return str(n)

    def content_bounds(self) -> tuple[float, float, float, float]:
        """Bounding box of existing shapes, in draw.io units."""
        root = self.model.find("root")
        boxes = []
        for child in list(root) if root is not None else []:
            geometry = child.find(".//mxGeometry")
            if geometry is None:
                continue
            try:
                boxes.append(
                    (
                        float(geometry.get("x") or 0),
                        float(geometry.get("y") or 0),
                        float(geometry.get("width") or 0),
                        float(geometry.get("height") or 0),
                    )
                )
            except ValueError:
                continue
        if not boxes:
            return (0.0, 0.0, 0.0, 0.0)
        left = min(b[0] for b in boxes)
        top = min(b[1] for b in boxes)
        right = max(b[0] + b[2] for b in boxes)
        bottom = max(b[1] + b[3] for b in boxes)
        return (left, top, right, bottom)

    def place(
        self,
        source: Path,
        *,
        x: float | None = None,
        y: float | None = None,
        width: float | None = None,
        height: float | None = None,
        attributes: dict[str, str] | None = None,
    ) -> str:
        """Embed a figure as a new shape, carrying its provenance from the start.

        This is the alternative to importing through draw.io's UI, which drops
        every trace of where the image came from. Placed this way the shape
        arrives with `src` and `hash` already on it, so it can be checked and
        refreshed without anyone having to reconstruct the link afterwards.
        """
        from ..assets import intrinsic_size

        root = self.model.find("root")
        if root is None:
            raise DocumentError("diagram has no <root>")

        data = source.read_bytes()
        media = MEDIA_TYPES.get(source.suffix.lower(), "image/png")
        payload = base64.b64encode(data).decode("ascii")

        # Size from the artwork itself, converted out of points into draw.io's
        # hundredths of an inch.
        natural = intrinsic_size(source)
        if width is None or height is None:
            if natural:
                width = width or from_points(natural["width"])
                height = height or from_points(natural["height"])
            else:
                width = width or 200.0
                height = height or 150.0
        elif height is None and natural and natural["width"]:
            height = width * (natural["height"] / natural["width"])

        if x is None or y is None:
            # Below whatever is already there, so a placed figure never lands on
            # top of existing work.
            _, _, _, bottom = self.content_bounds()
            x = 40.0 if x is None else x
            y = (bottom + 40.0) if y is None else y

        cell_id = self.next_free_id()
        wrapper = ET.SubElement(root, "object")
        wrapper.set("label", "")
        wrapper.set("id", cell_id)
        for key, value in (attributes or {}).items():
            wrapper.set(key, value)
        wrapper.set(ATTR_SRC, attributes.get(ATTR_SRC, "") if attributes else "")
        wrapper.set(
            ATTR_HASH, f"sha256:{hashlib.sha256(data).hexdigest()}"
        )

        cell = ET.SubElement(wrapper, "mxCell")
        cell.set(
            "style",
            "shape=image;verticalLabelPosition=bottom;labelBackgroundColor=default;"
            "verticalAlign=top;aspect=fixed;imageAspect=0;"
            f"image=data:{media},{payload};",
        )
        cell.set("vertex", "1")
        cell.set("parent", "1")

        geometry = ET.SubElement(cell, "mxGeometry")
        geometry.set("x", f"{x:g}")
        geometry.set("y", f"{y:g}")
        geometry.set("width", f"{width:g}")
        geometry.set("height", f"{height:g}")
        geometry.set("as", "geometry")

        return cell_id

    def reimport(self, keys: list[str] | None = None) -> list[tuple[str, str, str]]:
        """Re-embed components whose source file has changed.

        draw.io embeds a *copy*, with nothing pointing back at the original, so
        it can never notice that a plot was regenerated. This is the operation
        that closes that gap: for every shape with a declared `src`, compare the
        file on disk against the recorded `hash` and swap the data URI when they
        differ.

        Only the image payload and the hash change. Geometry, style, layers and
        every other shape are untouched, so a diagram someone has arranged by
        hand survives having its panels refreshed.

        Returns `(key, old_hash, new_hash)` for each component updated.
        """
        root = self.model.find("root")
        if root is None:
            return []

        updated: list[tuple[str, str, str]] = []
        for cell in self.cells():
            attributes = _provenance_attrs(
                cell.wrapper.attrib if cell.wrapper is not None else {}
            )
            src = attributes.get(ATTR_SRC) or attributes.get("path")
            if not src:
                continue
            if keys is not None and cell.cell_id not in keys:
                continue

            source = self._project_root / src
            if not source.is_file():
                continue

            current = f"sha256:{hashlib.sha256(source.read_bytes()).hexdigest()}"
            if current == attributes.get(ATTR_HASH):
                continue

            payload = base64.b64encode(source.read_bytes()).decode("ascii")
            media = MEDIA_TYPES.get(source.suffix.lower(), "image/png")
            style = cell.element.get("style") or ""
            new_style = _DATA_URI.sub(f"image=data:{media},{payload}", style, count=1)
            if new_style == style:
                continue

            cell.element.set("style", new_style)
            if cell.wrapper is not None:
                cell.wrapper.set(ATTR_HASH, current)
            updated.append((cell.cell_id, attributes.get(ATTR_HASH, ""), current))

        return updated

    def save(self) -> None:
        """Write the document back, preserving everything not touched.

        Refuses for `.drawio.svg`. Updating the embedded diagram is easy, but the
        rendered SVG body around it can only be regenerated by draw.io — so a
        write here would leave a file whose picture and whose metadata disagree,
        which is a worse outcome than refusing. Edit the `.drawio` and re-export.
        """
        if self.rendered:
            if self.path == self._source_path:
                raise DocumentError(
                    f"{self.path}: refusing to overwrite a rendered .drawio.svg "
                    f"— the picture and the embedded diagram would disagree. "
                    f"Write to a .drawio with `-o` and re-export with "
                    f"`drawio -x -f svg --embed-diagram`."
                )
            # Writing elsewhere is the safe handoff: emit the diagram alone, so
            # draw.io can re-render the picture around the updated XML.
            self._write_diagram(self.path)
            return
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        self.tree.write(temporary, encoding="utf-8", xml_declaration=False)
        temporary.replace(self.path)
