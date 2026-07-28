"""Composing a figmint document into a single publication-ready artifact.

The editor's canvas is SVG, and so is this. The difference is self-containment:
the canvas references each panel by URL, whereas a built figure inlines every
component so the result is one file you can drop into a manuscript.

Vector panels are inlined as SVG rather than embedded as images, so text in a
plot stays selectable text and lines stay lines all the way into the PDF. That
is the whole reason to go through SVG instead of rasterising panels.
"""

from __future__ import annotations

import base64
import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .document import Document

logger = logging.getLogger(__name__)

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

#: Raster formats we embed as data URIs rather than inlining.
RASTER_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class BuildError(RuntimeError):
    pass


@dataclass
class BuildResult:
    output: Path
    #: Panels that could not be inlined, with the reason.
    warnings: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt(value: float) -> str:
    """Trim float noise so the output diffs cleanly."""
    rounded = round(value, 3)
    return f"{rounded:g}"


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _attrs(pairs: dict[str, Any]) -> str:
    parts = []
    for key, value in pairs.items():
        if value is None or value == "":
            continue
        rendered = _fmt(value) if isinstance(value, (int, float)) else _esc(str(value))
        parts.append(f'{key}="{rendered}"')
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Inlining vector panels
# ---------------------------------------------------------------------------

_ID_ATTR = re.compile(r'\bid="([^"]+)"')
_URL_REF = re.compile(r"url\(#([^)]+)\)")
_HREF_REF = re.compile(r'(\b(?:xlink:href|href)=")#([^"]+)"')


def _namespace_ids(markup: str, prefix: str) -> str:
    """Rewrite every id and internal reference in an SVG fragment.

    Two matplotlib plots inlined into one document will both define `#DejaVuSans`
    glyph ids, `#clip1` clip paths, and so on. Without namespacing, the second
    panel silently adopts the first panel's definitions — which shows up as
    subtly wrong clipping or missing glyphs rather than an obvious error.
    """
    ids = set(_ID_ATTR.findall(markup))
    if not ids:
        return markup

    def rename(match: re.Match[str]) -> str:
        name = match.group(1)
        return f'id="{prefix}{name}"' if name in ids else match.group(0)

    def rename_url(match: re.Match[str]) -> str:
        name = match.group(1)
        return f"url(#{prefix}{name})" if name in ids else match.group(0)

    def rename_href(match: re.Match[str]) -> str:
        name = match.group(2)
        if name in ids:
            return f"{match.group(1)}#{prefix}{name}\""
        return match.group(0)

    markup = _ID_ATTR.sub(rename, markup)
    markup = _URL_REF.sub(rename_url, markup)
    markup = _HREF_REF.sub(rename_href, markup)
    return markup


def _parse_viewbox(root: ET.Element) -> tuple[float, float, float, float] | None:
    raw = root.get("viewBox")
    if not raw:
        return None
    parts = re.split(r"[\s,]+", raw.strip())
    if len(parts) != 4:
        return None
    try:
        return tuple(float(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def _intrinsic(root: ET.Element) -> tuple[float, float]:
    """Best-effort natural size of an SVG root, in user units."""
    from .assets import _length_to_pt  # local import to avoid a cycle at import time

    viewbox = _parse_viewbox(root)
    if viewbox:
        return viewbox[2], viewbox[3]
    width = _length_to_pt(root.get("width") or "")
    height = _length_to_pt(root.get("height") or "")
    if width and height:
        # _length_to_pt returns points; viewBox-less SVGs are treated as px.
        return width / 0.75, height / 0.75
    return 100.0, 100.0


def _fit_transform(
    box: dict[str, float],
    natural: tuple[float, float],
    fit: str,
) -> str:
    """Transform placing a panel's natural coordinate space into its box."""
    nat_w, nat_h = natural
    if nat_w <= 0 or nat_h <= 0:
        return f"translate({_fmt(box['x'])} {_fmt(box['y'])})"

    sx = box["width"] / nat_w
    sy = box["height"] / nat_h

    if fit == "fill":
        return (
            f"translate({_fmt(box['x'])} {_fmt(box['y'])}) "
            f"scale({_fmt(sx)} {_fmt(sy)})"
        )

    # `contain` (default) and `cover` keep the aspect ratio and centre the panel.
    scale = min(sx, sy) if fit != "cover" else max(sx, sy)
    dx = box["x"] + (box["width"] - nat_w * scale) / 2
    dy = box["y"] + (box["height"] - nat_h * scale) / 2
    return f"translate({_fmt(dx)} {_fmt(dy)}) scale({_fmt(scale)})"


def inline_svg(source: Path, box: dict[str, float], fit: str, prefix: str) -> str:
    """Inline an SVG file as a positioned `<g>`, keeping it vector."""
    markup = source.read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(markup)
    except ET.ParseError as exc:
        raise BuildError(f"{source.name}: not parseable as SVG ({exc})") from exc

    natural = _intrinsic(root)
    viewbox = _parse_viewbox(root)

    # Re-serialize the children so we keep everything (defs, styles, metadata)
    # but drop the nested <svg> element itself.
    inner = "".join(
        ET.tostring(child, encoding="unicode") for child in root
    )
    inner = _namespace_ids(inner, prefix)
    # ElementTree writes the default namespace onto every element; harmless but
    # noisy, and it re-declares SVG inside SVG.
    inner = inner.replace(f' xmlns="{SVG_NS}"', "")

    transform = _fit_transform(box, natural, fit)
    # A viewBox with a non-zero origin shifts the content; reproduce that.
    if viewbox and (viewbox[0] or viewbox[1]):
        transform += f" translate({_fmt(-viewbox[0])} {_fmt(-viewbox[1])})"

    return f'<g transform="{transform}">{inner}</g>'


def embed_raster(source: Path, box: dict[str, float], fit: str) -> str:
    """Embed a raster panel as a data URI so the output stays one file."""
    media_type = RASTER_TYPES.get(source.suffix.lower(), "application/octet-stream")
    payload = base64.b64encode(source.read_bytes()).decode("ascii")
    preserve = {
        "fill": "none",
        "cover": "xMidYMid slice",
    }.get(fit, "xMidYMid meet")
    return (
        f"<image {_attrs(dict(box, preserveAspectRatio=preserve))} "
        f'href="data:{media_type};base64,{payload}"/>'
    )


# ---------------------------------------------------------------------------
# Annotation nodes
# ---------------------------------------------------------------------------


def _text_svg(node: dict[str, Any]) -> str:
    style = node.get("style") or {}
    size = _num(style.get("fontSize"), 9)
    align = style.get("align", "left")
    anchor = {"center": "middle", "right": "end"}.get(align, "start")
    x = _num(node.get("x"))
    width = _num(node.get("width"))
    if align == "center":
        x += width / 2
    elif align == "right":
        x += width
    line_height = size * _num(style.get("lineHeight"), 1.2)

    lines = str(node.get("text", "")).split("\n")
    spans = "".join(
        f'<tspan x="{_fmt(x)}" dy="{_fmt(0 if i == 0 else line_height)}">{_esc(line)}</tspan>'
        for i, line in enumerate(lines)
    )
    return (
        "<text "
        + _attrs(
            {
                "x": x,
                "y": _num(node.get("y")) + size,
                "text-anchor": anchor,
                "font-family": style.get("fontFamily", "Helvetica, Arial, sans-serif"),
                "font-size": size,
                "font-weight": style.get("fontWeight"),
                "font-style": style.get("fontStyle"),
                "fill": style.get("color", "#111111"),
                "opacity": node.get("opacity"),
            }
        )
        + f">{spans}</text>"
    )


def _rect_svg(node: dict[str, Any]) -> str:
    style = node.get("style") or {}
    body = "<rect " + _attrs(
        {
            "x": _num(node.get("x")),
            "y": _num(node.get("y")),
            "width": _num(node.get("width")),
            "height": _num(node.get("height")),
            "rx": style.get("cornerRadius"),
            "fill": style.get("fill") or "none",
            "stroke": style.get("stroke", "#111111"),
            "stroke-width": style.get("strokeWidth", 1),
            "stroke-dasharray": style.get("strokeDash"),
            "opacity": node.get("opacity"),
        }
    ) + "/>"
    label = node.get("label")
    if label:
        body += (
            "<text "
            + _attrs(
                {
                    "x": _num(node.get("x")),
                    "y": _num(node.get("y")) - 2,
                    "font-size": 7,
                    "font-family": "Helvetica, Arial, sans-serif",
                    "fill": style.get("stroke", "#111111"),
                }
            )
            + f">{_esc(str(label))}</text>"
        )
    return body


def _ellipse_svg(node: dict[str, Any]) -> str:
    style = node.get("style") or {}
    return "<ellipse " + _attrs(
        {
            "cx": _num(node.get("x")) + _num(node.get("width")) / 2,
            "cy": _num(node.get("y")) + _num(node.get("height")) / 2,
            "rx": _num(node.get("width")) / 2,
            "ry": _num(node.get("height")) / 2,
            "fill": style.get("fill") or "none",
            "stroke": style.get("stroke", "#111111"),
            "stroke-width": style.get("strokeWidth", 1),
            "stroke-dasharray": style.get("strokeDash"),
            "opacity": node.get("opacity"),
        }
    ) + "/>"


def _arrow_svg(node: dict[str, Any]) -> str:
    style = node.get("style") or {}
    frm = node.get("from") or {"x": 0, "y": 1}
    to = node.get("to") or {"x": 1, "y": 0}
    x, y = _num(node.get("x")), _num(node.get("y"))
    w, h = _num(node.get("width")), _num(node.get("height"))
    x1, y1 = x + _num(frm.get("x")) * w, y + _num(frm.get("y")) * h
    x2, y2 = x + _num(to.get("x")) * w, y + _num(to.get("y")) * h

    curve = node.get("curve", "straight")
    if curve == "elbow":
        d = f"M {_fmt(x1)} {_fmt(y1)} L {_fmt(x2)} {_fmt(y1)} L {_fmt(x2)} {_fmt(y2)}"
    elif curve == "quad":
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        dx, dy = x2 - x1, y2 - y1
        d = (
            f"M {_fmt(x1)} {_fmt(y1)} Q {_fmt(mx - dy * 0.2)} {_fmt(my + dx * 0.2)} "
            f"{_fmt(x2)} {_fmt(y2)}"
        )
    else:
        d = f"M {_fmt(x1)} {_fmt(y1)} L {_fmt(x2)} {_fmt(y2)}"

    stroke = style.get("stroke", "#111111")
    body = "<path " + _attrs(
        {
            "d": d,
            "fill": "none",
            "stroke": stroke,
            "stroke-width": style.get("strokeWidth", 1),
            "stroke-dasharray": style.get("strokeDash"),
            "marker-end": "url(#fm-arrowhead)",
        }
    ) + "/>"
    label = node.get("label")
    if label:
        body += (
            "<text "
            + _attrs(
                {
                    "x": (x1 + x2) / 2,
                    "y": (y1 + y2) / 2 - 3,
                    "text-anchor": "middle",
                    "font-size": 7,
                    "font-family": "Helvetica, Arial, sans-serif",
                    "fill": stroke,
                }
            )
            + f">{_esc(str(label))}</text>"
        )
    return body


def _math_svg(node: dict[str, Any], warnings: list[str]) -> str:
    """Render a TeX node to vector paths via matplotlib's mathtext, if present.

    matplotlib is not a runtime dependency — it is simply very likely to be
    installed in a project that produces figures. When it is missing we fall
    back to `<foreignObject>` + MathML, which renders in browsers but is dropped
    by most SVG-to-PDF converters; the warning says so rather than letting the
    equation vanish silently from a PDF.
    """
    tex = str(node.get("tex", ""))
    x, y = _num(node.get("x")), _num(node.get("y"))
    size = _num(node.get("fontSize"), 10)
    color = node.get("color", "#111111")

    try:
        paths = _mathtext_paths(tex, size, color)
    except Exception as exc:  # noqa: BLE001 - any failure falls back
        warnings.append(
            f"math {node.get('id', '?')}: rendered as MathML, which most "
            f"SVG-to-PDF converters drop ({exc})"
        )
        from html import escape

        return (
            f'<foreignObject {_attrs({"x": x, "y": y, "width": _num(node.get("width"), 80), "height": _num(node.get("height"), 24)})}>'
            f'<div xmlns="http://www.w3.org/1999/xhtml" '
            f'style="font-size:{_fmt(size)}pt;color:{color}" '
            f'data-tex="{escape(tex, quote=True)}">{escape(tex)}</div>'
            f"</foreignObject>"
        )

    return f'<g transform="translate({_fmt(x)} {_fmt(y)})">{paths}</g>'


def _mathtext_paths(tex: str, size: float, color: str) -> str:
    """Convert TeX to SVG path data using matplotlib's mathtext engine."""
    from matplotlib.path import Path as MplPath
    from matplotlib.textpath import TextToPath
    from matplotlib.font_manager import FontProperties

    # matplotlib wants `$...$` around math; accept either form from the document.
    body = tex if tex.strip().startswith("$") else f"${tex}$"
    prop = FontProperties(size=size)
    vertices, codes = TextToPath().get_text_path(prop, body, ismath="TeX" == "never")

    path = MplPath(vertices, codes)
    commands: list[str] = []
    for points, code in path.iter_segments():
        if code == MplPath.MOVETO:
            commands.append(f"M {_fmt(points[0])} {_fmt(-points[1])}")
        elif code == MplPath.LINETO:
            commands.append(f"L {_fmt(points[0])} {_fmt(-points[1])}")
        elif code == MplPath.CURVE3:
            commands.append(
                f"Q {_fmt(points[0])} {_fmt(-points[1])} {_fmt(points[2])} {_fmt(-points[3])}"
            )
        elif code == MplPath.CURVE4:
            commands.append(
                f"C {_fmt(points[0])} {_fmt(-points[1])} {_fmt(points[2])} "
                f"{_fmt(-points[3])} {_fmt(points[4])} {_fmt(-points[5])}"
            )
        elif code == MplPath.CLOSEPOLY:
            commands.append("Z")

    if not commands:
        raise BuildError("mathtext produced no geometry")
    return f'<path d="{" ".join(commands)}" fill="{color}" stroke="none"/>'


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def compose_svg(document: Document) -> tuple[str, list[str]]:
    """Compose a document into one self-contained SVG string."""
    canvas = document.canvas
    width = _num(canvas.get("width"), 468)
    height = _num(canvas.get("height"), 210)
    warnings: list[str] = []

    parts: list[str] = [
        f'<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="{SVG_NS}" xmlns:xlink="{XLINK_NS}" '
        f'width="{_fmt(width)}pt" height="{_fmt(height)}pt" '
        f'viewBox="0 0 {_fmt(width)} {_fmt(height)}">',
        f"<title>{_esc(document.title or document.id)}</title>",
        "<defs>"
        '<marker id="fm-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/>'
        "</marker></defs>",
    ]

    background = canvas.get("background")
    if background:
        parts.append(
            f'<rect x="0" y="0" width="{_fmt(width)}" height="{_fmt(height)}" '
            f'fill="{_esc(str(background))}"/>'
        )

    for index, node in enumerate(document.nodes):
        if node.get("hidden"):
            continue
        kind = node.get("type")
        box = {
            "x": _num(node.get("x")),
            "y": _num(node.get("y")),
            "width": _num(node.get("width")),
            "height": _num(node.get("height")),
        }

        if kind == "image":
            key = str(node.get("source", ""))
            path = document.source_path(key)
            if path is None or not path.exists():
                warnings.append(f"panel {node.get('id', key)}: source `{key}` not found")
                continue
            fit = str(node.get("fit", "contain"))
            try:
                if path.suffix.lower() == ".svg":
                    parts.append(inline_svg(path, box, fit, f"p{index}-"))
                elif path.suffix.lower() in RASTER_TYPES:
                    parts.append(embed_raster(path, box, fit))
                else:
                    warnings.append(
                        f"panel {node.get('id', key)}: cannot embed {path.suffix} panels"
                    )
            except (BuildError, OSError) as exc:
                warnings.append(f"panel {node.get('id', key)}: {exc}")
        elif kind == "text":
            parts.append(_text_svg(node))
        elif kind == "math":
            parts.append(_math_svg(node, warnings))
        elif kind == "rect":
            parts.append(_rect_svg(node))
        elif kind == "ellipse":
            parts.append(_ellipse_svg(node))
        elif kind == "arrow":
            parts.append(_arrow_svg(node))
        else:
            warnings.append(f"node {node.get('id', '?')}: unknown type `{kind}`")

    parts.append("</svg>")
    return "\n".join(parts), warnings


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------


def _convert(svg: Path, target: Path) -> None:
    """Convert SVG to PDF/PNG with whatever converter is on the system."""
    suffix = target.suffix.lower()
    attempts: list[list[str]] = []
    if suffix == ".pdf":
        attempts = [
            ["rsvg-convert", "-f", "pdf", "-o", str(target), str(svg)],
            ["inkscape", str(svg), "--export-type=pdf", f"--export-filename={target}"],
            ["magick", str(svg), str(target)],
        ]
    elif suffix == ".png":
        attempts = [
            ["rsvg-convert", "-f", "png", "-d", "300", "-p", "300", "-o", str(target), str(svg)],
            [
                "inkscape",
                str(svg),
                "--export-type=png",
                "--export-dpi=300",
                f"--export-filename={target}",
            ],
            ["magick", "-density", "300", str(svg), str(target)],
        ]
    else:
        raise BuildError(f"unsupported output format: {suffix}")

    errors: list[str] = []
    for command in attempts:
        from shutil import which

        if which(command[0]) is None:
            continue
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0 and target.exists():
            return
        errors.append(f"{command[0]}: {result.stderr.strip()[:200]}")

    hint = "; ".join(errors) if errors else "no converter found"
    raise BuildError(
        f"could not produce {target.name} — install rsvg-convert or inkscape ({hint})"
    )


def build(
    document: Document,
    output: Path | None = None,
    formats: tuple[str, ...] = ("svg",),
) -> list[BuildResult]:
    """Build a document into one or more artifacts next to it."""
    svg_text, warnings = compose_svg(document)
    stem = document.path.name.removesuffix(".fig.yaml")
    base = output.parent / output.stem if output else document.path.with_name(stem)

    results: list[BuildResult] = []
    svg_path = Path(f"{base}.svg")
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(svg_text, encoding="utf-8")
    if "svg" in formats:
        results.append(BuildResult(svg_path, warnings))

    for fmt in formats:
        if fmt == "svg":
            continue
        target = Path(f"{base}.{fmt}")
        _convert(svg_path, target)
        results.append(BuildResult(target, warnings))

    # The SVG is an intermediate when it was not asked for.
    if "svg" not in formats:
        svg_path.unlink(missing_ok=True)

    return results
