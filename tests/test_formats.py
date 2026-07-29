"""Tests for the document adapter, and for reading `.drawio` in particular.

The `.drawio` fixture is a real file produced by draw.io's UI, not something
hand-written to be convenient. That matters: the whole difficulty of this format
is what the app actually emits on import — an anonymous base64 blob with no
record of where it came from — and a synthetic fixture would quietly assume the
problem away.
"""

from __future__ import annotations

import base64
import re
import shutil
import zlib
from pathlib import Path

import pytest

from figmint import formats
from figmint.cli import EXIT_ERROR, EXIT_OK, EXIT_STALE, main
from figmint.formats import DrawioDocument, FigYamlDocument, open_document
from figmint.formats.base import DocumentError, UnsupportedFormat
from figmint.formats.drawio import (
    UNITS_TO_POINTS,
    decode_data_uri,
    from_points,
    index_project,
    to_points,
)

EXAMPLES = Path(__file__).parent.parent / "examples"
DRAWIO = EXAMPLES / "test.drawio"


def strip_provenance(text: str) -> str:
    """Return the diagram as if freshly imported, with no provenance recorded.

    Covers both the plain keys figmint writes now and the legacy dotted form, so
    the fixture keeps working whichever the checked-in file happens to use.
    """
    return re.sub(r'\s(?:src|hash|figmint\.[a-z]+)="[^"]*"', "", text)


class TestDispatch:
    def test_fig_yaml_gets_the_yaml_reader(self):
        assert formats.reader_for(Path("a.fig.yaml")) is FigYamlDocument

    def test_drawio_gets_the_drawio_reader(self):
        assert formats.reader_for(Path("a.drawio")) is DrawioDocument
        assert formats.reader_for(Path("a.drawio.xml")) is DrawioDocument

    def test_the_longest_suffix_wins(self):
        # A bare `.yaml` is not a figure document; `.fig.yaml` is.
        assert formats.reader_for(Path("config.yaml")) is None

    def test_dispatch_is_case_insensitive(self):
        assert formats.reader_for(Path("A.DRAWIO")) is DrawioDocument

    def test_an_unknown_extension_raises(self, tmp_path: Path):
        target = tmp_path / "notes.txt"
        target.write_text("hello")
        with pytest.raises(UnsupportedFormat):
            open_document(target)


class TestUnits:
    def test_drawio_units_are_hundredths_of_an_inch(self):
        # Confirmed empirically: a 650-unit page exports as a 468pt PDF.
        assert to_points(650) == pytest.approx(468)
        assert UNITS_TO_POINTS == pytest.approx(0.72)

    def test_the_conversion_round_trips(self):
        assert from_points(to_points(123.4)) == pytest.approx(123.4)


class TestDataUris:
    def test_decodes_base64(self):
        uri = "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode()
        assert decode_data_uri(uri) == b"<svg/>"

    def test_decodes_percent_encoding(self):
        assert decode_data_uri("data:image/svg+xml,%3Csvg%2F%3E") == b"<svg/>"

    def test_an_empty_payload_is_none(self):
        assert decode_data_uri("data:image/svg+xml,") is None


@pytest.fixture(scope="module")
def document() -> DrawioDocument:
    doc = open_document(DRAWIO)
    assert isinstance(doc, DrawioDocument)
    return doc


class TestRealDrawioFile:
    def test_reads_the_page_size_in_points(self, document: DrawioDocument):
        # The fixture's page is 850x1100 draw.io units — US Letter.
        assert document.canvas.width == pytest.approx(612)
        assert document.canvas.height == pytest.approx(792)
        assert document.canvas.units == "pt"

    def test_finds_the_embedded_component(self, document: DrawioDocument):
        components = document.components()
        assert len(components) == 1
        assert components[0].is_embedded
        assert components[0].embedded is not None
        assert components[0].key == "7"

    def test_the_embedded_content_is_the_real_svg(self, document: DrawioDocument):
        embedded = document.components()[0].embedded
        assert embedded is not None
        # An XML declaration and DOCTYPE may precede the root element, so look
        # for the tag rather than requiring it first.
        assert b"<svg" in embedded
        # It is a matplotlib plot, which is what makes this a realistic case.
        assert b"matplotlib" in embedded

    def test_geometry_is_converted_to_points(self, document: DrawioDocument):
        # Derived from the file rather than hard-coded, so moving the shape in
        # draw.io does not break the test — what is being pinned is the unit
        # conversion, not the layout of somebody's diagram.
        import re

        raw = DRAWIO.read_text()
        geometry = re.search(
            r'id="7".*?<mxGeometry x="([\d.]+)" y="([\d.]+)"', raw, re.S
        )
        assert geometry, "cell 7 geometry not found"
        x, y = float(geometry.group(1)), float(geometry.group(2))

        node = document.nodes()[0]
        assert node["x"] == pytest.approx(x * 0.72)
        assert node["y"] == pytest.approx(y * 0.72)

    def test_ignores_non_image_shapes(self, document: DrawioDocument):
        # The fixture also contains ellipses, a LaTeX text cell, and a stock
        # gear icon; only the imported image counts as a component.
        assert len(document.components()) == 1


class TestBundledAssets:
    """draw.io's own clipart is not a figure component.

    The fixture uses a stock gear icon (`image=img/clipart/Gear_128x128.png`).
    Counting bundled shapes as components would make every diagram containing a
    gear or a cloud fail its provenance check, which teaches people to ignore
    the warnings — worse than not checking at all.
    """

    def test_stock_clipart_is_not_a_component(self, document: DrawioDocument):
        origins = [c.origin for c in document.components()]
        assert not any("img/clipart" in (o or "") for o in origins)

    def test_only_the_imported_image_counts(self, document: DrawioDocument):
        assert len(document.components()) == 1
        assert document.components()[0].is_embedded

    @pytest.mark.parametrize(
        "value",
        [
            "img/clipart/Gear_128x128.png",
            "stencils/network/router.svg",
            "https://example.com/remote.png",
        ],
    )
    def test_bundled_and_remote_references_are_skipped(self, value: str):
        from figmint.formats.drawio import _image_from_style

        assert _image_from_style(f"shape=image;image={value};") is None

    def test_a_project_relative_reference_still_counts(self):
        from figmint.formats.drawio import _image_from_style

        assert (
            _image_from_style("shape=image;image=figures/plot.svg;")
            == "figures/plot.svg"
        )

    def test_an_embedded_uri_always_counts(self):
        from figmint.formats.drawio import _image_from_style

        assert _image_from_style(
            "shape=image;image=data:image/svg+xml,PHN2Zy8+;"
        ).startswith("data:")


class TestOriginRecovery:
    """Recovering an origin draw.io did not record."""

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / ".git").mkdir()
        shutil.copy(DRAWIO, tmp_path / "d.drawio")
        # Strip any figmint attributes, so this is a freshly-imported diagram.
        target = tmp_path / "d.drawio"
        target.write_text(strip_provenance(target.read_text()))
        return tmp_path

    def test_an_unmatched_blob_has_no_origin(self, project: Path):
        document = open_document(project / "d.drawio")
        assert document.components()[0].origin == ""

    def test_content_hash_matching_recovers_the_origin(self, project: Path):
        # Save the original into the project, as a plotting script would.
        document = open_document(project / "d.drawio")
        embedded = document.components()[0].embedded
        assert embedded is not None
        (project / "figures").mkdir()
        (project / "figures" / "plot.svg").write_bytes(embedded)

        # Re-read: the blob is now identifiable without anyone recording it.
        again = open_document(project / "d.drawio")
        assert again.components()[0].origin == "figures/plot.svg"

    def test_a_declared_attribute_beats_hash_matching(self, project: Path):
        target = project / "d.drawio"
        target.write_text(
            target.read_text().replace(
                '<mxCell id="7"', '<mxCell id="7" data-x="1"', 1
            )
        )
        # Wrap the cell and declare a path that no hash would produce.
        document = open_document(target)
        document.annotate({"7": {"src": "declared/elsewhere.svg"}})
        document.save()

        assert open_document(target).components()[0].origin == (
            "declared/elsewhere.svg"
        )


#: A shape in the plain form: a bare `<mxCell>` carrying the image style.
BARE_CELL_DOC = (
    '<mxfile><diagram id="d" name="P">'
    '<mxGraphModel pageWidth="650" pageHeight="292"><root>'
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<mxCell id="p" value="" style="shape=image;image=data:image/svg+xml,PHN2Zy8+" '
    'vertex="1" parent="1">'
    '<mxGeometry x="0" y="0" width="100" height="100" as="geometry"/>'
    "</mxCell></root></mxGraphModel></diagram></mxfile>"
)

#: The other form draw.io writes: an `<object>` carrying the style itself, with
#: no nested `<mxCell>` at all.
STYLED_OBJECT_DOC = (
    '<mxfile><diagram id="d" name="P">'
    '<mxGraphModel pageWidth="650" pageHeight="292"><root>'
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<object label="" property="mine" id="p" '
    'style="shape=image;image=data:image/svg+xml,PHN2Zy8+" vertex="1" parent="1">'
    '<mxGeometry x="0" y="0" width="100" height="100" as="geometry"/>'
    "</object></root></mxGraphModel></diagram></mxfile>"
)


class TestStyledObjectForm:
    """`<object style=…>` with no nested `<mxCell>`.

    Requiring the nested cell made a diagram full of images report *zero*
    components, so every policy check passed vacuously. For a provenance tool
    that is the worst available failure, and it is why this form gets its own
    tests.
    """

    @pytest.fixture
    def target(self, tmp_path: Path) -> Path:
        path = tmp_path / "styled.drawio"
        path.write_text(STYLED_OBJECT_DOC)
        return path

    def test_the_component_is_found(self, target: Path):
        assert len(open_document(target).components()) == 1

    def test_geometry_is_read(self, target: Path):
        node = open_document(target).nodes()[0]
        assert node["width"] == pytest.approx(72)

    def test_annotating_normalises_the_flattened_form(self, target: Path):
        # draw.io writes this form but cannot render it — an empty canvas and a
        # failed export. Since the shape is being rewritten anyway, repair it.
        document = open_document(target)
        assert document.annotate({"src": "x.svg"} and {"p": {"src": "x.svg"}}) == 1
        document.save()
        text = target.read_text()
        assert 'src="x.svg"' in text
        assert "<mxCell" in text.split("<object")[1], "should gain a nested cell"
        assert 'property="mine"' in text, "user data must survive"

    def test_declared_attributes_are_read_back(self, target: Path):
        document = open_document(target)
        document.annotate({"p": {"src": "figures/known.svg"}})
        document.save()
        assert open_document(target).components()[0].origin == "figures/known.svg"


class TestAnnotate:
    @pytest.fixture
    def target(self, tmp_path: Path) -> Path:
        path = tmp_path / "d.drawio"
        shutil.copy(DRAWIO, path)
        path.write_text(strip_provenance(path.read_text()))
        return path

    def test_wraps_a_bare_cell_in_an_object(self, target: Path):
        document = open_document(target)
        assert document.annotate({"7": {"src": "figures/p.svg"}}) == 1
        document.save()
        assert "<object" in target.read_text()
        assert 'src="figures/p.svg"' in target.read_text()

    def test_the_id_moves_to_the_wrapper_only(self, tmp_path: Path):
        # Built explicitly rather than from the fixture, so this pins behaviour
        # for the bare-mxCell form regardless of how the fixture evolves.
        target = tmp_path / "bare.drawio"
        target.write_text(BARE_CELL_DOC)

        document = open_document(target)
        assert document.annotate({"p": {"src": "x.svg"}}) == 1
        document.save()

        text = target.read_text()
        wrapper = re.search(r"<object[^>]*>", text)
        assert wrapper and 'id="p"' in wrapper.group(0)
        # Leaving the id on both makes draw.io treat them as two cells.
        inner = re.search(r"<object[^>]*>\s*<mxCell([^>]*)>", text)
        assert inner and 'id="p"' not in inner.group(1)

    def test_annotating_twice_does_not_nest_wrappers(self, target: Path):
        for _ in range(2):
            document = open_document(target)
            document.annotate({"7": {"src": "x.svg"}})
            document.save()
        assert target.read_text().count("<object") == 1

    def test_existing_custom_properties_are_preserved(self, target: Path):
        # Someone may already have used draw.io's Edit Data on the shape. Build
        # that state through the wrapper the app itself produces, then annotate.
        document = open_document(target)
        document.annotate({"7": {"property": "mine"}})
        document.save()
        assert 'property="mine"' in target.read_text()

        again = open_document(target)
        again.annotate({"7": {"src": "x.svg"}})
        again.save()

        text = target.read_text()
        assert 'property="mine"' in text, "annotating must not drop other data"
        assert 'src="x.svg"' in text

    def test_value_moves_off_the_nested_cell(self, tmp_path: Path):
        """draw.io discards the wrapper if the nested cell still has `value`.

        Found by round-tripping through draw.io's own CLI: an `<object>` whose
        `<mxCell>` retains a `value` attribute gets collapsed back to a plain
        cell on save, taking every custom attribute with it. Copying `value` into
        the wrapper's `label` is not enough — it has to be removed.
        """
        target = tmp_path / "bare.drawio"
        target.write_text(BARE_CELL_DOC)

        document = open_document(target)
        document.annotate({"p": {"src": "figures/plot.svg"}})
        document.save()

        text = target.read_text()
        nested = re.search(r"<object[^>]*>\s*<mxCell([^>]*)>", text)
        assert nested, "expected a nested mxCell"
        assert "value=" not in nested.group(1), (
            "leaving value on the nested cell makes draw.io drop the wrapper"
        )

    def test_the_label_is_carried_to_the_wrapper(self, tmp_path: Path):
        target = tmp_path / "labelled.drawio"
        target.write_text(BARE_CELL_DOC.replace('value=""', 'value="Panel A"'))

        document = open_document(target)
        document.annotate({"p": {"src": "x.svg"}})
        document.save()

        wrapper = re.search(r"<object[^>]*>", target.read_text())
        assert wrapper and 'label="Panel A"' in wrapper.group(0)

    def test_unknown_cell_ids_are_ignored(self, target: Path):
        document = open_document(target)
        assert document.annotate({"does-not-exist": {"src": "x"}}) == 0


class TestCompressedDiagrams:
    def test_reads_a_deflated_diagram(self, tmp_path: Path):
        # The web app stores the model base64+deflate'd; reading only plain XML
        # would fail on files people actually have.
        inner = (
            '<mxGraphModel pageWidth="650" pageHeight="292"><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="p" style="shape=image;image=data:image/svg+xml,'
            "PHN2Zy8+" '" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="10" height="10" as="geometry"/>'
            "</mxCell></root></mxGraphModel>"
        )
        import urllib.parse

        deflated = zlib.compressobj(9, zlib.DEFLATED, -15)
        payload = deflated.compress(urllib.parse.quote(inner).encode())
        payload += deflated.flush()
        encoded = base64.b64encode(payload).decode()

        target = tmp_path / "c.drawio"
        target.write_text(
            f'<mxfile><diagram id="a" name="P">{encoded}</diagram></mxfile>'
        )
        document = open_document(target)
        assert document.canvas.width == pytest.approx(468)
        assert len(document.components()) == 1

    def test_a_corrupt_diagram_raises_a_clear_error(self, tmp_path: Path):
        target = tmp_path / "bad.drawio"
        target.write_text('<mxfile><diagram id="a">!!!not base64!!!</diagram></mxfile>')
        with pytest.raises(DocumentError):
            open_document(target)

    def test_a_file_with_no_diagram_raises(self, tmp_path: Path):
        target = tmp_path / "empty.drawio"
        target.write_text("<mxfile></mxfile>")
        with pytest.raises(DocumentError):
            open_document(target)


class TestProjectIndex:
    def test_indexes_figures_by_hash(self, tmp_path: Path):
        (tmp_path / "a.svg").write_bytes(b"<svg/>")
        index = index_project(tmp_path)
        assert "a.svg" in index.values()

    def test_skips_noise_directories(self, tmp_path: Path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "a.svg").write_bytes(b"<svg/>")
        assert index_project(tmp_path) == {}

    def test_ignores_non_figure_files(self, tmp_path: Path):
        (tmp_path / "notes.txt").write_text("hello")
        assert index_project(tmp_path) == {}


class TestFigYamlThroughTheAdapter:
    def test_reads_the_example(self):
        document = open_document(EXAMPLES / "two-panel.fig.yaml")
        assert document.id == "fig-turbine-performance"
        assert document.canvas.width == 468
        assert not document.embeds_components

    def test_components_are_referenced_not_embedded(self):
        document = open_document(EXAMPLES / "two-panel.fig.yaml")
        components = document.components()
        assert len(components) == 2
        assert all(not c.is_embedded for c in components)
        assert all(c.recorded_hash for c in components)

    def test_content_comes_from_disk(self):
        document = open_document(EXAMPLES / "two-panel.fig.yaml")
        content = document.components()[0].content()
        assert content is not None and content.lstrip().startswith(b"<svg")


class TestCli:
    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / ".git").mkdir()
        target = tmp_path / "d.drawio"
        shutil.copy(DRAWIO, target)
        target.write_text(strip_provenance(target.read_text()))
        return tmp_path

    def test_check_fails_on_an_anonymous_component(self, project: Path, capsys):
        assert main(["check", str(project / "d.drawio")]) == EXIT_STALE
        out = capsys.readouterr().out
        assert "anonymous embedded content" in out
        assert "figmint adopt" in out

    def test_adopt_cannot_identify_what_is_not_in_the_project(
        self, project: Path, capsys
    ):
        assert main(["adopt", str(project / "d.drawio")]) == EXIT_STALE
        assert "matches no file" in capsys.readouterr().err

    def test_adopt_then_check_passes(self, project: Path, capsys):
        document = open_document(project / "d.drawio")
        embedded = document.components()[0].embedded
        assert embedded is not None
        (project / "plot.svg").write_bytes(embedded)

        assert main(["adopt", str(project / "d.drawio")]) == EXIT_OK
        assert "identified" in capsys.readouterr().out
        assert main(["check", str(project / "d.drawio")]) == EXIT_OK

    def test_a_declared_origin_that_has_gone_is_reported(
        self, project: Path, capsys
    ):
        # The figure still renders from the embedded copy, which is exactly why
        # a vanished original needs saying out loud rather than passing quietly.
        document = open_document(project / "d.drawio")
        document.annotate({"7": {"src": "figures/deleted.svg"}})
        document.save()

        assert main(["check", str(project / "d.drawio")]) == EXIT_STALE
        out = capsys.readouterr().out
        assert "origin missing" in out

    def test_adopt_on_a_fig_yaml_is_a_no_op(self, capsys, tmp_path: Path):
        shutil.copy(EXAMPLES / "two-panel.fig.yaml", tmp_path / "d.fig.yaml")
        shutil.copytree(EXAMPLES / "figures", tmp_path / "figures")
        assert main(["adopt", str(tmp_path / "d.fig.yaml")]) == EXIT_OK
        assert "nothing to adopt" in capsys.readouterr().out


class TestReimport:
    """Refreshing embedded copies when their source changes.

    draw.io embeds a copy with no link back to the original, so a regenerated
    plot can never propagate on its own. This is the operation that closes that
    gap without disturbing anything the author arranged by hand.
    """

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / ".git").mkdir()
        target = tmp_path / "d.drawio"
        target.write_text(strip_provenance(DRAWIO.read_text()))

        # Put the embedded plot on disk and link it, as `adopt` would.
        document = open_document(target)
        (tmp_path / "plot.svg").write_bytes(document.components()[0].embedded)
        assert main(["adopt", str(target)]) == EXIT_OK
        return tmp_path

    def test_nothing_to_do_when_the_source_is_unchanged(self, project: Path):
        document = open_document(project / "d.drawio")
        assert document.reimport() == []

    def test_a_changed_source_is_re_embedded(self, project: Path):
        (project / "plot.svg").write_bytes(b"<svg>new content</svg>")

        document = open_document(project / "d.drawio")
        updated = document.reimport()
        assert len(updated) == 1
        document.save()

        again = open_document(project / "d.drawio")
        assert again.components()[0].embedded == b"<svg>new content</svg>"

    def test_the_recorded_hash_is_updated(self, project: Path):
        (project / "plot.svg").write_bytes(b"<svg>new</svg>")
        document = open_document(project / "d.drawio")
        document.reimport()
        document.save()

        component = open_document(project / "d.drawio").components()[0]
        assert component.recorded_hash == component.embedded_hash()

    def test_layout_survives_a_refresh(self, project: Path):
        before = open_document(project / "d.drawio").nodes()[0]
        (project / "plot.svg").write_bytes(b"<svg>new</svg>")

        document = open_document(project / "d.drawio")
        document.reimport()
        document.save()

        after = open_document(project / "d.drawio").nodes()[0]
        assert (after["x"], after["y"], after["width"]) == (
            before["x"],
            before["y"],
            before["width"],
        )

    def test_other_shapes_are_untouched(self, project: Path):
        target = project / "d.drawio"
        (project / "plot.svg").write_bytes(b"<svg>new</svg>")
        document = open_document(target)
        document.reimport()
        document.save()

        text = target.read_text()
        # The LaTeX cell, the gear and the sketch rectangle must all survive.
        assert "C_P" in text
        assert "Gear_128x128" in text
        assert "sketch=1" in text

    def test_a_component_with_no_declared_source_is_skipped(self, tmp_path: Path):
        target = tmp_path / "anon.drawio"
        target.write_text(strip_provenance(DRAWIO.read_text()))
        assert open_document(target).reimport() == []

    def test_a_missing_source_is_skipped_not_erased(self, project: Path):
        (project / "plot.svg").unlink()
        document = open_document(project / "d.drawio")
        assert document.reimport() == []
        # The embedded copy is still the only version left; deleting it would
        # destroy the figure.
        assert document.components()[0].embedded is not None

    def test_reimport_is_idempotent(self, project: Path):
        (project / "plot.svg").write_bytes(b"<svg>new</svg>")
        document = open_document(project / "d.drawio")
        document.reimport()
        document.save()
        # A second run must be a no-op, or a pipeline stage would never settle.
        assert open_document(project / "d.drawio").reimport() == []


class TestReimportCli:
    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / ".git").mkdir()
        target = tmp_path / "d.drawio"
        target.write_text(strip_provenance(DRAWIO.read_text()))
        document = open_document(target)
        (tmp_path / "plot.svg").write_bytes(document.components()[0].embedded)
        main(["adopt", str(target)])
        return tmp_path

    def test_check_reports_without_writing(self, project: Path, capsys):
        target = project / "d.drawio"
        before = target.read_text()
        (project / "plot.svg").write_bytes(b"<svg>new</svg>")

        assert main(["reimport", str(target), "--check"]) == EXIT_STALE
        assert "out of date" in capsys.readouterr().out
        assert target.read_text() == before, "--check must not modify the file"

    def test_check_passes_when_current(self, project: Path):
        assert main(["reimport", str(project / "d.drawio"), "--check"]) == EXIT_OK

    def test_output_leaves_the_source_untouched(self, project: Path):
        # This is what keeps a DVC stage acyclic: the authored diagram stays a
        # pure input and the refreshed one is a pure output.
        target = project / "d.drawio"
        before = target.read_text()
        (project / "plot.svg").write_bytes(b"<svg>new</svg>")

        derived = project / "built.drawio"
        assert main(["reimport", str(target), "-o", str(derived)]) == EXIT_OK
        assert target.read_text() == before
        assert derived.exists()
        assert open_document(derived).components()[0].embedded == b"<svg>new</svg>"

    def test_reimport_on_a_fig_yaml_is_a_no_op(self, tmp_path: Path, capsys):
        shutil.copy(EXAMPLES / "two-panel.fig.yaml", tmp_path / "d.fig.yaml")
        shutil.copytree(EXAMPLES / "figures", tmp_path / "figures")
        assert main(["reimport", str(tmp_path / "d.fig.yaml")]) == EXIT_OK
        assert "referenced" in capsys.readouterr().out


class TestPlace:
    """Embedding a figure with provenance attached from the start.

    The point of this path is that it never creates the problem `adopt` exists
    to clean up: the shape arrives knowing where it came from.
    """

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / ".git").mkdir()
        shutil.copy(EXAMPLES / "figures" / "cp_curve.svg", tmp_path / "plot.svg")
        # With its sidecar, so the figure clears the default provenance bar —
        # the refusal path gets its own test below.
        shutil.copy(
            EXAMPLES / "figures" / "cp_curve.svg.prov.yaml",
            tmp_path / "plot.svg.prov.yaml",
        )
        return tmp_path

    def test_creates_a_diagram_when_asked(self, project: Path):
        target = project / "new.drawio"
        assert (
            main(["place", str(project / "plot.svg"), "--into", str(target), "--create"])
            == EXIT_OK
        )
        assert target.exists()

    def test_refuses_to_create_without_the_flag(self, project: Path, capsys):
        target = project / "missing.drawio"
        assert main(["place", str(project / "plot.svg"), "--into", str(target)]) != EXIT_OK
        assert "--create" in capsys.readouterr().err
        assert not target.exists()

    def test_the_shape_carries_src_and_hash(self, project: Path):
        target = project / "d.drawio"
        main(["place", str(project / "plot.svg"), "--into", str(target), "--create"])

        component = open_document(target).components()[0]
        assert component.origin == "plot.svg"
        assert component.recorded_hash == component.embedded_hash()

    def test_the_figure_is_embedded_not_referenced(self, project: Path):
        target = project / "d.drawio"
        main(["place", str(project / "plot.svg"), "--into", str(target), "--create"])
        component = open_document(target).components()[0]
        assert component.is_embedded
        assert component.embedded == (project / "plot.svg").read_bytes()

    def test_size_comes_from_the_artwork(self, project: Path):
        target = project / "d.drawio"
        main(["place", str(project / "plot.svg"), "--into", str(target), "--create"])
        # The SVG is 216x162pt, which is 300x225 in draw.io units.
        node = open_document(target).nodes()[0]
        assert node["width"] == pytest.approx(216, abs=1)
        assert node["height"] == pytest.approx(162, abs=1)

    def test_a_second_figure_does_not_land_on_the_first(self, project: Path):
        target = project / "d.drawio"
        main(["place", str(project / "plot.svg"), "--into", str(target), "--create"])
        main(["place", str(project / "plot.svg"), "--into", str(target)])

        nodes = open_document(target).nodes()
        assert len(nodes) == 2
        first, second = nodes
        assert second["y"] >= first["y"] + first["height"], "should stack, not overlap"

    def test_ids_do_not_collide(self, project: Path):
        target = project / "d.drawio"
        main(["place", str(project / "plot.svg"), "--into", str(target), "--create"])
        main(["place", str(project / "plot.svg"), "--into", str(target)])
        keys = [c.key for c in open_document(target).components()]
        assert len(set(keys)) == len(keys)

    def test_explicit_geometry_is_respected(self, project: Path):
        target = project / "d.drawio"
        main([
            "place", str(project / "plot.svg"), "--into", str(target), "--create",
            "--x", "50", "--y", "60", "--width", "120",
        ])
        node = open_document(target).nodes()[0]
        assert node["x"] == pytest.approx(50 * 0.72)
        assert node["y"] == pytest.approx(60 * 0.72)

    def test_an_unidentified_figure_is_refused(self, project: Path, capsys):
        anon = project / "anon.svg"
        anon.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
        target = project / "d.drawio"

        assert (
            main(["place", str(anon), "--into", str(target), "--create"]) == EXIT_STALE
        )
        assert "refusing to place" in capsys.readouterr().err

    def test_force_overrides_the_policy(self, project: Path):
        anon = project / "anon.svg"
        anon.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
        target = project / "d.drawio"
        assert (
            main([
                "place", str(anon), "--into", str(target), "--create", "--force",
            ])
            == EXIT_OK
        )

    def test_a_missing_figure_is_an_error(self, project: Path, capsys):
        assert main([
            "place", str(project / "ghost.svg"),
            "--into", str(project / "d.drawio"), "--create",
        ]) == EXIT_ERROR
        assert "no such file" in capsys.readouterr().err

    def test_a_placed_figure_can_be_refreshed(self, project: Path):
        # The whole point: placed with provenance, so `reimport` can find it.
        target = project / "d.drawio"
        main(["place", str(project / "plot.svg"), "--into", str(target), "--create"])
        (project / "plot.svg").write_bytes(b"<svg>regenerated</svg>")

        document = open_document(target)
        assert len(document.reimport()) == 1
        document.save()
        assert open_document(target).components()[0].embedded == b"<svg>regenerated</svg>"
