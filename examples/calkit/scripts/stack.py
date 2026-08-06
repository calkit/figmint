"""Stack two SVG panels into one figure.

The composite step, without a desktop application in the way. Each panel is
nested as an `<svg>` element inside a wrapper, which is plain SVG and keeps the
panels as vectors — the point being that this artifact is *derived from other
artifacts*, so its provenance chain is two levels deep and figmint has
something to check that no single command produced.

The real reason it exists in this example is what happens either side of it:
the panels are recorded unsigned because they never leave the repository, and
this figure is signed because it does.
"""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PANELS = ("cp_curve.svg", "ct_curve.svg")
GAP = 30


def dimensions(markup: str) -> tuple[float, float]:
    """The panel's own width and height, in whatever units it declared."""
    found = {}
    for name in ("width", "height"):
        match = re.search(rf'<svg[^>]*?\b{name}="([0-9.]+)', markup)
        if not match:
            raise SystemExit(f"panel has no {name}; cannot lay it out")
        found[name] = float(match.group(1))
    return found["width"], found["height"]


def body(markup: str) -> str:
    """The panel with its XML prolog and DOCTYPE stripped.

    Only one of those may appear in a document, and it has to be at the top, so
    a nested panel that kept its own would produce a file no renderer accepts.
    """
    start = markup.index("<svg")
    return markup[start:]


def main() -> None:
    figures = HERE / "figures"
    panels = [(figures / name).read_text(encoding="utf-8") for name in PANELS]
    sizes = [dimensions(p) for p in panels]
    width = sum(w for w, _ in sizes) + GAP * (len(panels) - 1)
    height = max(h for _, h in sizes)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width:g}" height="{height:g}" '
        f'viewBox="0 0 {width:g} {height:g}">'
    ]
    offset = 0.0
    for (panel_width, panel_height), markup, label in zip(
        sizes, panels, "ab"
    ):
        parts.append(
            f'<svg x="{offset:g}" y="0" width="{panel_width:g}" '
            f'height="{panel_height:g}">{body(markup)}</svg>'
        )
        parts.append(
            f'<text x="{offset + 8:g}" y="20" '
            f'font-family="sans-serif" font-size="15" '
            f'font-weight="bold">({label})</text>'
        )
        offset += panel_width + GAP
    parts.append("</svg>")

    out = figures / "performance.svg"
    out.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out.relative_to(HERE)}")


if __name__ == "__main__":
    main()
