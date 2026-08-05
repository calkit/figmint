"""Embedding an image into a `.drawio` diagram, with its origin attached.

draw.io's Electron sandbox refuses to load local files during export, so an
imported image has to be **embedded** in the document as a base64 data URI. That
has one consequence worth stating plainly: importing through draw.io's own
Insert > Image destroys every trace of where the image came from. What lands in
the file is an anonymous blob.

Worse, draw.io resizes anything over 1200 px through a canvas before embedding
it, which re-encodes the bytes — so any Content Credentials the image carried are
gone, including an AI-generation disclosure. Verified directly: a 1408 px
generated PNG went in signed and came out with nothing.

`figmint drawio import` writes the original bytes verbatim and attaches `src`
and `hash` to the shape, then records the diagram in `figmint.toml` with each
embedded image as an input. So the diagram stays editable in draw.io, the
credentials survive into the export, and `figmint status` can tell you when a
panel has been regenerated since it was embedded.

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
import shutil
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .sign import MEDIA_TYPES
from .store import Artifact, Input, Store, hash_file

logger = logging.getLogger(__name__)

#: draw.io units are 1/100 inch; points are 1/72 inch.
UNITS_TO_POINTS = 72.0 / 100.0

#: Provenance keys, written as plain shape attributes.
#:
#: Deliberately unprefixed. A `figmint.`-style prefix would namespace these away
#: from a user's own data, but draw.io strips the dotted prefix when it re-saves
#: a file — observed directly: `figmint.path` came back as `path`. So a prefix
#: buys nothing and silently changes the keys under us.
ATTR_SRC = "src"
ATTR_HASH = "hash"

#: Attributes draw.io owns; never treated as provenance.
RESERVED_ATTRS = frozenset(
    {
        "label",
        "placeholders",
        "tooltip",
        "link",
        "id",
        "style",
        "parent",
        "vertex",
        "edge",
    }
)

#: Provenance keys figmint understands. Anything else on the shape is left
#: alone, so a user's own annotations survive a round-trip.
PROVENANCE_ATTRS = frozenset(
    {ATTR_SRC, ATTR_HASH, "doi", "url", "citation", "license", "note"}
)

#: Legacy dotted form, still read so older files keep working.
LEGACY_PREFIX = "figmint."

_DATA_URI = re.compile(r'image=(data:[^;,]+(?:;base64)?,[^;"]*)')

EMPTY = (
    '<mxfile host="figmint"><diagram id="{id}" name="{page}">'
    '<mxGraphModel dx="800" dy="600" grid="0" gridSize="10" guides="1" '
    'tooltips="1" connect="1" arrows="1" fold="0" page="0" pageScale="1" '
    'pageWidth="850" pageHeight="1100" math="1" shadow="0">'
    '<root><mxCell id="0" /><mxCell id="1" parent="0" /></root>'
    "</mxGraphModel></diagram></mxfile>"
)


class DrawioError(RuntimeError):
    """Raised when a diagram cannot be read or written."""


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
        raise DrawioError("diagram is empty")

    try:
        raw = base64.b64decode(text)
    except (binascii.Error, ValueError) as exc:
        raise DrawioError(f"diagram is neither XML nor base64: {exc}") from exc

    try:
        # Raw deflate, no zlib header.
        inflated = zlib.decompress(raw, -15)
    except zlib.error as exc:
        raise DrawioError(f"could not inflate diagram: {exc}") from exc

    try:
        xml_text = urllib.parse.unquote(inflated.decode("utf-8"))
        return ET.fromstring(xml_text)
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise DrawioError(f"inflated diagram is not valid XML: {exc}") from exc


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
    if value.startswith(BUNDLED_PREFIXES) or value.startswith(
        EXTERNAL_SCHEMES
    ):
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


@dataclass
class Embedded:
    """One image embedded in a diagram."""

    shape_id: str
    src: str | None
    hash: str | None
    payload: bytes | None

    @property
    def embedded_hash(self) -> str | None:
        if self.payload is None:
            return None
        return f"sha256:{hashlib.sha256(self.payload).hexdigest()}"


@dataclass
class Diagram:
    """A `.drawio` (or `.drawio.svg`) document, opened for reading or writing."""

    path: Path
    tree: ET.ElementTree
    model: ET.Element
    #: True for a `.drawio.svg`, which carries the diagram inside a rendered
    #: picture. figmint will not write one back: it can update the embedded XML
    #: but not the rendering around it, and a file whose picture and metadata
    #: disagree is worse than one that refuses to be written.
    rendered: bool = False

    @classmethod
    def open(cls, path: Path) -> "Diagram":
        path = Path(path)
        if not path.is_file():
            raise DrawioError(f"no such diagram: {path}")
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            raise DrawioError(f"{path}: {exc}") from exc

        root = tree.getroot()
        rendered = root.tag.endswith("svg")
        if rendered:
            content = root.get("content")
            if not content:
                raise DrawioError(
                    f"{path}: an SVG with no embedded diagram; export from "
                    f"draw.io with --embed-diagram"
                )
            try:
                root = ET.fromstring(content)
            except ET.ParseError as exc:
                raise DrawioError(
                    f"{path}: embedded diagram is not XML: {exc}"
                ) from exc
            tree = ET.ElementTree(root)

        diagram = root.find("diagram") if root.tag == "mxfile" else None
        model = (
            decode_diagram(diagram)
            if diagram is not None
            else root.find(".//mxGraphModel")
        )
        if model is None:
            raise DrawioError(f"{path}: no <mxGraphModel> found")
        return cls(path=path, tree=tree, model=model, rendered=rendered)

    @classmethod
    def create(cls, path: Path, page: str = "Page-1") -> "Diagram":
        root = ET.fromstring(EMPTY.format(id=Path(path).stem, page=page))
        tree = ET.ElementTree(root)
        model = root.find(".//mxGraphModel")
        return cls(path=Path(path), tree=tree, model=model)

    # -- reading ----------------------------------------------------------

    def embedded(self) -> list[Embedded]:
        """Every image in the diagram, with whatever provenance it carries."""
        found: list[Embedded] = []
        root = self.model.find("root")
        if root is None:
            return found

        # Cells nested inside an <object> are that object's own shape, not
        # separate images. ElementTree has no parent pointer, so they are
        # collected up front rather than tested for one.
        wrapped = {
            id(cell)
            for wrapper in root.iter("object")
            for cell in wrapper.iter("mxCell")
        }

        for node in root.iter():
            if node.tag == "object":
                attrs = _provenance_attrs(node.attrib)
                cell = node.find("mxCell")
                style = (
                    cell.get("style") if cell is not None else None
                ) or node.get("style", "")
                uri = _image_from_style(style)
                if uri is None and not attrs:
                    continue
                found.append(
                    Embedded(
                        shape_id=node.get("id", ""),
                        src=attrs.get(ATTR_SRC) or None,
                        hash=attrs.get(ATTR_HASH) or None,
                        payload=_payload_bytes(uri) if uri else None,
                    )
                )
            elif node.tag == "mxCell" and id(node) not in wrapped:
                # A bare cell carries no attributes, so it is only interesting
                # when it holds an image — an anonymous one, imported through
                # draw.io's own UI.
                uri = _image_from_style(node.get("style", ""))
                if uri and node.find("mxGeometry") is not None:
                    found.append(
                        Embedded(
                            shape_id=node.get("id", ""),
                            src=None,
                            hash=None,
                            payload=_payload_bytes(uri),
                        )
                    )
        return found

    def next_free_id(self) -> str:
        used = {
            node.get("id")
            for node in self.model.iter()
            if node.get("id") is not None
        }
        candidate = 2
        while str(candidate) in used:
            candidate += 1
        return str(candidate)

    def content_bounds(self) -> tuple[float, float, float, float]:
        boxes = []
        for geometry in self.model.iter("mxGeometry"):
            try:
                boxes.append(
                    (
                        float(geometry.get("x", 0)),
                        float(geometry.get("y", 0)),
                        float(geometry.get("width", 0)),
                        float(geometry.get("height", 0)),
                    )
                )
            except (TypeError, ValueError):
                continue
        if not boxes:
            return (0.0, 0.0, 0.0, 0.0)
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[0] + b[2] for b in boxes),
            max(b[1] + b[3] for b in boxes),
        )

    # -- writing ----------------------------------------------------------

    def shape_for(self, relative: str):
        """The wrapper already carrying this source path, if the diagram has one."""
        root = self.model.find("root")
        if root is None:
            return None
        for wrapper in root.findall("object"):
            if wrapper.get(ATTR_SRC) == relative:
                return wrapper
        return None

    def geometry_for(
        self, relative: str
    ) -> tuple[float, float, float, float] | None:
        """An existing panel's box, so replacing it does not rearrange the page.

        Size as well as position. A panel is almost always resized once it is on
        the canvas — that is what laying out a composite *is* — and refreshing
        the picture inside it must not throw that away and snap it back to the
        image's natural dimensions.
        """
        wrapper = self.shape_for(relative)
        if wrapper is None:
            return None
        geometry = wrapper.find("mxCell/mxGeometry")
        if geometry is None:
            return None
        try:
            return (
                float(geometry.get("x", 0)),
                float(geometry.get("y", 0)),
                float(geometry.get("width", 0)),
                float(geometry.get("height", 0)),
            )
        except (TypeError, ValueError):
            return None

    def place(
        self,
        source: Path,
        *,
        relative: str,
        x: float | None = None,
        y: float | None = None,
        width: float | None = None,
        height: float | None = None,
    ) -> str:
        """Embed an image as a new shape, carrying its origin from the start."""
        root = self.model.find("root")
        if root is None:
            raise DrawioError("diagram has no <root>")

        data = Path(source).read_bytes()
        media = MEDIA_TYPES.get(Path(source).suffix.lower(), "image/png")
        payload = base64.b64encode(data).decode("ascii")

        natural = intrinsic_size(Path(source))
        aspect = None
        if natural and natural[0] and natural[1]:
            aspect = natural[1] / natural[0]

        prior = self.geometry_for(relative)
        if prior is not None and width is None and height is None:
            # An existing panel's box wins over the artwork's natural size:
            # refreshing the picture inside a laid-out composite must not
            # resize it.
            width, height = prior[2] or None, prior[3] or None

        # Giving one dimension scales the other by the artwork's aspect ratio;
        # asking for a 300-unit-wide panel and getting one at the image's full
        # natural height would be a surprise.
        if width is None and height is None:
            if natural:
                width, height = (
                    from_points(natural[0]),
                    from_points(natural[1]),
                )
            else:
                width, height = 200.0, 150.0
        elif height is None:
            height = width * aspect if aspect else width * 0.75
        elif width is None:
            width = height / aspect if aspect else height / 0.75

        if prior is not None:
            # Replacing a panel keeps where the author put it and how big they
            # made it, unless this call says otherwise.
            x = prior[0] if x is None else x
            y = prior[1] if y is None else y
        if x is None or y is None:
            # Below whatever is already there, so a placed image never lands on
            # top of existing work.
            _, _, _, bottom = self.content_bounds()
            x = 40.0 if x is None else x
            y = (bottom + 40.0) if y is None else y

        # A panel already in the diagram is *replaced*, not added again. When a
        # figure is regenerated the diagram holds a stale copy of it, and the
        # only sane repair is to refresh that copy in place — appending a second
        # one would leave the old bytes on the canvas beside the new, which is
        # both wrong on the page and wrong in the record.
        existing = self.shape_for(relative)
        if existing is not None:
            wrapper = existing
            shape_id = wrapper.get("id") or self.next_free_id()
            # Keep whatever the author arranged: position and size are their
            # layout decisions, not something an import should undo.
            cell = wrapper.find("mxCell")
            if cell is not None:
                wrapper.remove(cell)
        else:
            shape_id = self.next_free_id()
            wrapper = ET.SubElement(root, "object")
            wrapper.set("label", "")
            wrapper.set("id", shape_id)
        wrapper.set(ATTR_SRC, relative)
        wrapper.set(ATTR_HASH, f"sha256:{hashlib.sha256(data).hexdigest()}")

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
        geometry.set("x", _number(x))
        geometry.set("y", _number(y))
        geometry.set("width", _number(width))
        geometry.set("height", _number(height))
        geometry.set("as", "geometry")
        return shape_id

    def refresh(self, root_dir: Path) -> list[str]:
        """Re-embed every panel whose source file has moved on.

        A diagram holds a *copy* of each panel, and the copy is what draw.io
        renders. Once a figure is redrawn the copy is stale, and re-importing by
        hand to fix that is busywork the diagram already has the information to
        avoid: each shape carries the `src` it came from and the hash it had.

        Position and size are the author's; only the picture inside is
        replaced. Returns the paths refreshed, so the caller can say what it
        did rather than change files silently.
        """
        refreshed: list[str] = []
        for item in self.embedded():
            if not item.src:
                continue
            source = Path(root_dir) / item.src
            if not source.is_file():
                # Nothing to refresh from. `figmint status` reports the missing
                # input; failing the export here would only block the one
                # command that could still produce something useful.
                continue
            current = hash_file(source)
            if current == (item.hash or item.embedded_hash):
                continue
            self.place(source, relative=item.src)
            refreshed.append(item.src)
        return refreshed

    def save(self) -> None:
        if self.rendered:
            raise DrawioError(
                f"{self.path} is a rendered .drawio.svg. figmint can update the "
                f"diagram but not the picture drawn around it; write to a "
                f".drawio and re-export from draw.io."
            )
        self.tree.write(self.path, encoding="utf-8", xml_declaration=False)


def _number(value: float) -> str:
    """Geometry as draw.io writes it: no trailing zeros on whole numbers."""
    rounded = round(float(value), 3)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def intrinsic_size(path: Path) -> tuple[float, float] | None:
    """Natural size in points, or None when it cannot be read.

    Used only to seed a sensible aspect ratio, so failing to parse a format is
    not an error.
    """
    import struct

    suffix = path.suffix.lower()
    try:
        data = path.read_bytes()
    except OSError:
        return None

    if suffix == ".png" and data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        # PNG is in pixels; 96 dpi is the web default draw.io assumes.
        return (width * 72 / 96, height * 72 / 96)

    if suffix == ".svg":
        text = data[:4096].decode("utf-8", "replace")
        width = re.search(r'width="([\d.]+)', text)
        height = re.search(r'height="([\d.]+)', text)
        if width and height:
            return (float(width.group(1)), float(height.group(1)))
        box = re.search(r'viewBox="[\d.\s-]*?([\d.]+)[\s,]+([\d.]+)"', text)
        if box:
            return (float(box.group(1)), float(box.group(2)))

    if suffix in (".jpg", ".jpeg"):
        index = 2
        while index < len(data) - 9:
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in range(0xC0, 0xD0) and marker not in (
                0xC4,
                0xC8,
                0xCC,
            ):
                height, width = struct.unpack(
                    ">HH", data[index + 5 : index + 9]
                )
                return (width * 72 / 96, height * 72 / 96)
            length = struct.unpack(">H", data[index + 2 : index + 4])[0]
            index += 2 + length
    return None


# ---------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------


@dataclass
class ImportResult:
    image: str
    diagram: str
    shape_id: str
    artifact: Artifact


def import_image(
    image: Path,
    diagram: Path,
    *,
    x: float | None = None,
    y: float | None = None,
    width: float | None = None,
) -> ImportResult:
    """Embed an image into a diagram and record the diagram's provenance."""
    image = Path(image)
    diagram = Path(diagram)
    if not image.is_file():
        raise DrawioError(f"no such image: {image}")
    if diagram.suffix.lower() == ".svg" or diagram.name.endswith(
        ".drawio.svg"
    ):
        raise DrawioError(
            "cannot import into a rendered .drawio.svg; import into the "
            ".drawio and re-export from draw.io"
        )

    store = Store.for_path(diagram if diagram.exists() else diagram.parent)
    document = (
        Diagram.open(diagram) if diagram.exists() else Diagram.create(diagram)
    )
    shape_id = document.place(
        image, relative=store.relative(image), x=x, y=y, width=width
    )
    document.save()

    # Every embedded image is an input of the diagram, so `figmint status`
    # reports the composite as stale when any panel is regenerated.
    inputs = [
        Input(item.src, item.hash)
        for item in document.embedded()
        if item.src and item.hash
    ]
    artifact = Artifact(
        path=store.relative(diagram),
        hash=hash_file(diagram),
        inputs=inputs,
        command=None,
        kind="authored",
    )
    store.record(artifact)
    store.save()

    return ImportResult(
        image=store.relative(image),
        diagram=store.relative(diagram),
        shape_id=shape_id,
        artifact=artifact,
    )


@dataclass
class ExportResult:
    diagram: str
    output: str
    artifact: Artifact
    signed: bool
    #: Panels whose embedded copy was stale and has been re-embedded. Reported
    #: rather than done quietly: the diagram is a file the author owns.
    refreshed: list[str] = field(default_factory=list)


def export(
    diagram: Path,
    output: Path,
    *,
    cert: Path | None = None,
    key: Path | None = None,
    sign: bool = True,
    drawio_bin: str | None = None,
) -> ExportResult:
    """Render a diagram to a publishable picture, and record the derivation.

    A `.drawio` is a source. It is not a picture anything but draw.io can
    display, so publishing means exporting — and the exported file is the one
    that leaves the repository, which makes it the one where an embedded
    manifest earns its keep.

    Exported with `--embed-diagram`, so the shapes' `src` and `hash` attributes
    survive into the output. A plain export keeps the picture and drops the
    metadata, leaving a figure that looks identical and can no longer be
    checked.

    figmint does not render the picture itself — draw.io does. The diagram is
    recorded as the output's input, so the chain runs
    `data -> panel -> diagram -> picture` with every link hashed.

    Export doubles as the point at which the diagram itself is re-recorded.
    Editing a diagram in draw.io is the whole reason for keeping one, and every
    such edit changes its bytes — so without this the composite would sit
    permanently `modified`, and the one signal that actually means *somebody
    wrote this file behind figmint's back* would be worthless. Exporting is the
    deliberate act that says "this arrangement is the one I meant".

    The panels are named as ingredients of the picture as well as of the
    diagram. Otherwise an AI-generated panel's disclosure stops at the `.drawio`
    — which carries no manifest of its own — and the exported figure, the file
    that actually travels, would claim to be an ordinary composite.
    """
    diagram = Path(diagram)
    output = Path(output)
    if not diagram.is_file():
        raise DrawioError(f"no such diagram: {diagram}")

    executable = drawio_bin or shutil.which("drawio")
    if executable is None:
        raise DrawioError(
            "the draw.io desktop app is not on PATH. Install it "
            "(`brew install --cask drawio`) or pass --drawio."
        )

    suffix = output.suffix.lower().lstrip(".")
    if suffix not in ("svg", "png", "pdf", "jpg", "jpeg"):
        raise DrawioError(f"draw.io cannot export to {output.suffix}")

    # Refresh stale panels first, because draw.io renders the *copy* inside the
    # diagram. Each shape already carries the `src` it came from and the hash it
    # had, so nothing needs re-importing by hand: a redrawn figure is picked up
    # here, its picture replaced and its hash updated, with the layout the
    # author gave it left alone.
    #
    # Before the render, obviously — refreshing afterwards would publish the old
    # panels and only take effect on the next export.
    store = Store.for_path(diagram)
    document = Diagram.open(diagram)
    refreshed = document.refresh(store.root)
    if refreshed:
        document.save()

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [executable, "-x", "-f", suffix, "--yes"]
    if suffix == "svg":
        # Without this the picture is kept and the provenance is dropped: the
        # `src`/`hash` attributes live on mxCells that do not survive into a
        # rendered SVG unless the diagram rides along.
        command.append("--embed-diagram")
    command += ["-o", str(output), str(diagram)]

    completed = subprocess.run(command, capture_output=True, text=True)
    if not output.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()
        raise DrawioError(
            f"draw.io did not write {output.name}"
            + (f":\n{detail}" if detail else "")
        )

    inputs = [Input(store.relative(diagram), hash_file(diagram))]

    # Re-record the diagram: exporting is the act that blesses the arrangement.
    document = Diagram.open(diagram)
    panels = [
        Input(item.src, item.hash)
        for item in document.embedded()
        if item.src and item.hash
    ]
    store.record(
        Artifact(
            path=store.relative(diagram),
            hash=hash_file(diagram),
            inputs=panels,
            kind="authored",
        )
    )

    # The command as a reader could run it, not the internal subcommand string.
    # A record that names something unrunnable is worse than one that names
    # nothing: it looks like a reproduction recipe and is not one.
    recorded_command = f"figmint drawio export {store.relative(diagram)} {store.relative(output)}"

    signed = False
    if sign:
        from . import credentials as credentials_mod
        from . import sign as sign_mod

        if output.suffix.lower() in credentials_mod.SIGNABLE_SUFFIXES:
            try:
                # Before hashing: embedding changes the bytes.
                # Panels as well as the diagram: an AI-generated panel's
                # disclosure lives in its own manifest, and the `.drawio` has
                # none to carry it onward.
                sign_mod.sign_artifact(
                    output,
                    inputs + panels,
                    store.root,
                    cert=cert,
                    key=key,
                    command=recorded_command,
                )
                signed = True
            except sign_mod.SigningError as exc:
                raise DrawioError(
                    f"could not sign {output.name}: {exc}"
                ) from exc

    artifact = Artifact(
        path=store.relative(output),
        hash=hash_file(output),
        inputs=inputs,
        command=recorded_command,
        kind="drawio-export",
        signed=signed,
    )
    store.record(artifact)
    store.save()

    return ExportResult(
        diagram=store.relative(diagram),
        output=store.relative(output),
        artifact=artifact,
        refreshed=refreshed,
        signed=signed,
    )
