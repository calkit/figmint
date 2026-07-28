"""Tests for composing a document into a single self-contained artifact."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from figmint.build import (
    BuildError,
    _fit_transform,
    _namespace_ids,
    build,
    compose_svg,
    inline_svg,
)
from figmint.document import load

EXAMPLES = Path(__file__).parent.parent / "examples"
DOC = EXAMPLES / "two-panel.fig.yaml"


@pytest.fixture(scope="module")
def composed() -> str:
    svg, warnings = compose_svg(load(DOC))
    assert warnings == [], warnings
    return svg


class TestComposition:
    def test_canvas_size_is_emitted_in_points(self, composed: str):
        # Points are the whole reason the figure lands at true size in a PDF.
        assert 'width="468pt"' in composed
        assert 'height="210pt"' in composed
        assert 'viewBox="0 0 468 210"' in composed

    def test_output_is_self_contained(self, composed: str):
        # No references back out to the figure directory.
        assert "figures/cp_curve.svg" not in composed
        assert "figures/wake_profile.svg" not in composed

    def test_vector_panels_are_inlined_not_rasterised(self, composed: str):
        # Content unique to the panel artwork should appear literally.
        assert "Tip speed ratio" in composed
        assert "Spanwise position" in composed
        assert "data:image" not in composed

    def test_annotations_are_rendered(self, composed: str):
        assert "(a)" in composed
        assert "(b)" in composed
        assert "#d1495b" in composed  # the ROI box stroke

    def test_hidden_nodes_are_skipped(self):
        document = load(DOC)
        document.nodes[2]["hidden"] = True
        svg, _ = compose_svg(document)
        assert "(a)" not in svg

    def test_missing_source_warns_rather_than_failing(self, tmp_path: Path):
        document = load(DOC)
        document.sources["cp-curve"]["path"] = "figures/does_not_exist.svg"
        svg, warnings = compose_svg(document)
        assert any("not found" in w for w in warnings)
        # The rest of the figure still composes.
        assert "Spanwise position" in svg

    def test_unknown_node_type_warns(self):
        document = load(DOC)
        document.nodes.append({"id": "weird", "type": "hologram", "x": 0, "y": 0})
        _, warnings = compose_svg(document)
        assert any("hologram" in w for w in warnings)

    def test_xml_is_well_formed(self, composed: str):
        import xml.etree.ElementTree as ET

        # The strongest single check that inlining did not corrupt anything.
        ET.fromstring(composed)


class TestMath:
    def test_latex_becomes_vector_paths(self, composed: str):
        # matplotlib is a dev dependency here, so mathtext should succeed and
        # there should be no MathML fallback.
        assert "foreignObject" not in composed

    def test_fallback_is_used_when_mathtext_fails(self, monkeypatch):
        import figmint.build as build_mod

        def boom(*args, **kwargs):
            raise RuntimeError("no matplotlib")

        monkeypatch.setattr(build_mod, "_mathtext_paths", boom)
        svg, warnings = compose_svg(load(DOC))
        assert "foreignObject" in svg
        # The warning must say the equation will not survive PDF conversion,
        # rather than letting it disappear quietly.
        assert any("SVG-to-PDF" in w for w in warnings)

    def test_tex_is_preserved_in_the_fallback(self, monkeypatch):
        import figmint.build as build_mod

        monkeypatch.setattr(
            build_mod, "_mathtext_paths", lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
        )
        svg, _ = compose_svg(load(DOC))
        assert "data-tex=" in svg


class TestIdNamespacing:
    """Inlining several SVGs into one document collides their internal ids."""

    FRAGMENT = (
        '<defs><clipPath id="clip1"><rect/></clipPath>'
        '<path id="glyph-A" d="M0 0"/></defs>'
        '<g clip-path="url(#clip1)"><use xlink:href="#glyph-A"/></g>'
    )

    def test_ids_are_prefixed(self):
        out = _namespace_ids(self.FRAGMENT, "p3-")
        assert 'id="p3-clip1"' in out
        assert 'id="p3-glyph-A"' in out

    def test_internal_references_follow_the_rename(self):
        out = _namespace_ids(self.FRAGMENT, "p3-")
        assert "url(#p3-clip1)" in out
        assert 'xlink:href="#p3-glyph-A"' in out
        # Nothing may still point at the unprefixed name.
        assert "url(#clip1)" not in out
        assert 'href="#glyph-A"' not in out

    def test_unknown_references_are_left_alone(self):
        # A reference to an id defined elsewhere must not be rewritten.
        out = _namespace_ids('<g fill="url(#external)"/>', "p1-")
        assert "url(#external)" in out

    def test_fragment_without_ids_is_unchanged(self):
        assert _namespace_ids("<g><rect/></g>", "p1-") == "<g><rect/></g>"

    def test_two_panels_sharing_ids_stay_independent(self, tmp_path: Path):
        # The real failure mode: two matplotlib plots both defining #clip1.
        panel = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            '<defs><clipPath id="clip1"><rect width="50" height="50"/></clipPath></defs>'
            '<rect width="100" height="100" clip-path="url(#clip1)"/>'
            "</svg>"
        )
        for name in ("a.svg", "b.svg"):
            (tmp_path / name).write_text(panel)

        box = {"x": 0, "y": 0, "width": 100, "height": 100}
        first = inline_svg(tmp_path / "a.svg", box, "contain", "p0-")
        second = inline_svg(tmp_path / "b.svg", box, "contain", "p1-")
        combined = first + second

        assert combined.count('id="p0-clip1"') == 1
        assert combined.count('id="p1-clip1"') == 1
        assert "url(#p0-clip1)" in combined
        assert "url(#p1-clip1)" in combined


class TestFit:
    BOX = {"x": 10.0, "y": 20.0, "width": 200.0, "height": 100.0}

    def test_fill_stretches_both_axes_independently(self):
        out = _fit_transform(self.BOX, (100, 100), "fill")
        assert "scale(2 1)" in out

    def test_contain_preserves_aspect_and_centres(self):
        out = _fit_transform(self.BOX, (100, 100), "contain")
        # Limited by height: scale 1, centred horizontally in a 200-wide box.
        assert "scale(1)" in out
        assert "translate(60 20)" in out

    def test_cover_fills_the_box(self):
        out = _fit_transform(self.BOX, (100, 100), "cover")
        assert "scale(2)" in out

    def test_degenerate_natural_size_does_not_divide_by_zero(self):
        out = _fit_transform(self.BOX, (0, 0), "contain")
        assert "translate(10 20)" in out


class TestInlineSvg:
    def test_non_svg_content_raises(self, tmp_path: Path):
        bad = tmp_path / "bad.svg"
        bad.write_text("this is not xml <<<")
        with pytest.raises(BuildError):
            inline_svg(bad, {"x": 0, "y": 0, "width": 10, "height": 10}, "contain", "p-")

    def test_viewbox_origin_offset_is_applied(self, tmp_path: Path):
        panel = tmp_path / "offset.svg"
        panel.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="10 20 100 100"><rect/></svg>'
        )
        out = inline_svg(
            panel, {"x": 0, "y": 0, "width": 100, "height": 100}, "contain", "p-"
        )
        assert "translate(-10 -20)" in out


class TestBuildOutputs:
    def test_writes_svg_next_to_the_document(self, tmp_path: Path):
        import shutil

        target = tmp_path / "two-panel.fig.yaml"
        shutil.copy(DOC, target)
        shutil.copytree(EXAMPLES / "figures", tmp_path / "figures")

        results = build(load(target), formats=("svg",))
        assert len(results) == 1
        assert results[0].output == tmp_path / "two-panel.svg"
        assert results[0].output.exists()

    def test_output_path_override_is_respected(self, tmp_path: Path):
        import shutil

        target = tmp_path / "two-panel.fig.yaml"
        shutil.copy(DOC, target)
        shutil.copytree(EXAMPLES / "figures", tmp_path / "figures")

        out = tmp_path / "dist" / "figure-1.svg"
        results = build(load(target), output=out, formats=("svg",))
        assert results[0].output == out
        assert out.exists()

    def test_rebuilding_is_deterministic(self, tmp_path: Path):
        # Byte-identical rebuilds keep the output reviewable in version control.
        first, _ = compose_svg(load(DOC))
        second, _ = compose_svg(load(DOC))
        assert first == second

    def test_no_float_noise_in_output(self, composed: str):
        assert not re.search(r"\d\.\d{6,}", composed)
