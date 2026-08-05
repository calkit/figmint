"""`figmint drawio import`.

The reason this command exists: draw.io's own Insert > Image resizes anything
over 1200 px through a canvas before embedding it, which re-encodes the bytes
and destroys any Content Credentials the image carried — including an
AI-generation disclosure. It also discards the source path entirely.

So the properties worth testing are that the bytes go in untouched and the
origin goes in with them.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from figmint.drawio import Diagram, DrawioError, export, import_image
from figmint.status import State, check_path
from figmint.store import Store, hash_file


def _png(path: Path, size: tuple[int, int] = (400, 200)) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(figsize=(size[0] / 100, size[1] / 100))
    axes.plot([1, 2, 3], [1, 4, 9])
    figure.savefig(path)
    plt.close(figure)
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "figures").mkdir()
    _png(tmp_path / "figures" / "plot.png")
    return tmp_path


def embedded_bytes(diagram: Path) -> bytes:
    text = diagram.read_text(encoding="utf-8")
    match = re.search(r"image=data:[^;,]+,([A-Za-z0-9+/=]+)", text)
    assert match, "no embedded image found"
    return base64.b64decode(match.group(1))


class TestImport:
    def test_it_creates_a_diagram_that_does_not_exist(self, project: Path):
        result = import_image(
            project / "figures/plot.png", project / "composite.drawio"
        )
        assert (project / "composite.drawio").is_file()
        assert result.shape_id

    def test_the_bytes_go_in_untouched(self, project: Path):
        """The whole point. draw.io's own import re-encodes anything over
        1200px, which silently destroys Content Credentials."""
        source = project / "figures/plot.png"
        import_image(source, project / "composite.drawio")
        assert (
            embedded_bytes(project / "composite.drawio") == source.read_bytes()
        )

    def test_the_shape_carries_src_and_hash(self, project: Path):
        import_image(
            project / "figures/plot.png", project / "composite.drawio"
        )
        text = (project / "composite.drawio").read_text(encoding="utf-8")
        assert 'src="figures/plot.png"' in text
        assert f'hash="{hash_file(project / "figures/plot.png")}"' in text

    def test_the_diagram_is_recorded_with_the_image_as_an_input(
        self, project: Path
    ):
        import_image(
            project / "figures/plot.png", project / "composite.drawio"
        )
        artifact = Store.load(project).get("composite.drawio")
        assert artifact is not None
        assert artifact.kind == "authored"
        assert [i.path for i in artifact.inputs] == ["figures/plot.png"]

    def test_a_regenerated_panel_makes_the_composite_stale(
        self, project: Path
    ):
        """draw.io holds a *copy* with no link back, so nothing in the app can
        notice this. It is the reason the import is recorded at all."""
        import_image(
            project / "figures/plot.png", project / "composite.drawio"
        )
        assert check_path(project / "composite.drawio").state is State.OK

        _png(project / "figures" / "plot.png", size=(500, 250))
        report = check_path(project / "composite.drawio")
        assert report.state is State.STALE
        assert [i.path for i in report.changed_inputs] == ["figures/plot.png"]

    def test_a_second_image_does_not_land_on_the_first(self, project: Path):
        _png(project / "figures" / "other.png")
        import_image(
            project / "figures/plot.png", project / "composite.drawio"
        )
        import_image(
            project / "figures/other.png", project / "composite.drawio"
        )

        geometries = re.findall(
            r'<mxGeometry x="([\d.]+)" y="([\d.]+)"',
            (project / "composite.drawio").read_text(encoding="utf-8"),
        )
        assert len(geometries) == 2
        assert float(geometries[1][1]) > float(geometries[0][1])

    def test_both_images_become_inputs(self, project: Path):
        _png(project / "figures" / "other.png")
        import_image(
            project / "figures/plot.png", project / "composite.drawio"
        )
        result = import_image(
            project / "figures/other.png", project / "composite.drawio"
        )
        assert len(result.artifact.inputs) == 2

    def test_ids_do_not_collide(self, project: Path):
        _png(project / "figures" / "other.png")
        first = import_image(
            project / "figures/plot.png", project / "composite.drawio"
        )
        second = import_image(
            project / "figures/other.png", project / "composite.drawio"
        )
        assert first.shape_id != second.shape_id

    def test_aspect_ratio_is_preserved_when_only_width_is_given(
        self, project: Path
    ):
        """Asking for a 300-unit-wide panel and getting the image's full natural
        height would be a surprise."""
        import_image(
            project / "figures/plot.png",
            project / "composite.drawio",
            width=300,
        )
        match = re.search(
            r'width="([\d.]+)" height="([\d.]+)"',
            (project / "composite.drawio").read_text(encoding="utf-8"),
        )
        width, height = float(match.group(1)), float(match.group(2))
        assert width == 300
        assert 100 < height < 200  # 2:1 artwork


class TestRefusals:
    def test_a_missing_image_is_an_error(self, project: Path):
        with pytest.raises(DrawioError, match="no such image"):
            import_image(project / "figures/gone.png", project / "c.drawio")

    def test_importing_into_a_rendered_svg_is_refused(self, project: Path):
        """figmint can update the embedded diagram but not the picture drawn
        around it, and a file whose image and metadata disagree is worse than
        one that refuses to be written."""
        (project / "c.drawio.svg").write_text(
            "<svg content='&lt;mxfile/&gt;'/>"
        )
        with pytest.raises(DrawioError, match="rendered .drawio.svg"):
            import_image(
                project / "figures/plot.png", project / "c.drawio.svg"
            )

    def test_an_unparseable_diagram_is_an_error(self, project: Path):
        (project / "broken.drawio").write_text("<<<not xml")
        with pytest.raises(DrawioError):
            import_image(
                project / "figures/plot.png", project / "broken.drawio"
            )


class TestReading:
    def test_embedded_images_are_listed_with_their_origin(self, project: Path):
        import_image(
            project / "figures/plot.png", project / "composite.drawio"
        )
        items = Diagram.open(project / "composite.drawio").embedded()
        assert len(items) == 1
        assert items[0].src == "figures/plot.png"
        assert items[0].embedded_hash == items[0].hash


class TestExport:
    """`figmint drawio export` — the step that makes a diagram publishable."""

    def fake_drawio(
        self, tmp_path: Path, monkeypatch, writes: bytes = b"<svg/>"
    ):
        """Stand in for the desktop app: record the argv and write the output."""
        seen: dict = {}

        def fake_run(command, **kwargs):
            import subprocess as sp

            seen["command"] = command
            Path(command[command.index("-o") + 1]).write_bytes(writes)
            return sp.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(
            "figmint.drawio.shutil.which", lambda name: "/fake/drawio"
        )
        monkeypatch.setattr("figmint.drawio.subprocess.run", fake_run)
        return seen

    def test_svg_is_exported_with_the_diagram_embedded(
        self, project: Path, monkeypatch
    ):
        """A plain export keeps the picture and drops the provenance: `src` and
        `hash` live on mxCells that do not survive into a rendered SVG."""
        seen = self.fake_drawio(project, monkeypatch)
        import_image(project / "figures/plot.png", project / "c.drawio")
        export(project / "c.drawio", project / "c.svg", sign=False)
        assert "--embed-diagram" in seen["command"]

    def test_the_flag_is_only_for_svg(self, project: Path, monkeypatch):
        seen = self.fake_drawio(project, monkeypatch)
        import_image(project / "figures/plot.png", project / "c.drawio")
        export(project / "c.drawio", project / "c.png", sign=False)
        assert "--embed-diagram" not in seen["command"]

    def test_the_output_is_recorded_against_the_diagram(
        self, project: Path, monkeypatch
    ):
        self.fake_drawio(project, monkeypatch)
        import_image(project / "figures/plot.png", project / "c.drawio")
        export(project / "c.drawio", project / "c.svg", sign=False)
        artifact = Store.load(project).get("c.svg")
        assert artifact.kind == "drawio-export"
        assert [i.path for i in artifact.inputs] == ["c.drawio"]

    def test_export_re_records_the_edited_diagram(
        self, project: Path, monkeypatch
    ):
        """Editing a diagram in draw.io is the whole reason for keeping one, and
        every edit changes its bytes. Without this the composite would sit
        permanently `modified` and that signal would be worth nothing."""
        self.fake_drawio(project, monkeypatch)
        import_image(project / "figures/plot.png", project / "c.drawio")

        # Somebody arranges it in draw.io.
        text = (project / "c.drawio").read_text(encoding="utf-8")
        (project / "c.drawio").write_text(
            text.replace("</root>", '<mxCell id="9" value="note"/></root>')
        )
        # Not a finding: nothing produced the diagram, so a person rearranging
        # it in draw.io is normal authoring, and the export below is what
        # carries the change onward.
        assert check_path(project / "c.drawio").state is State.OK

        export(project / "c.drawio", project / "c.svg", sign=False)
        assert check_path(project / "c.drawio").state is State.OK

    def test_the_panels_stay_recorded_across_a_re_record(
        self, project: Path, monkeypatch
    ):
        """Re-recording must not quietly drop the inputs; the composite would
        then look self-contained and never go stale."""
        self.fake_drawio(project, monkeypatch)
        import_image(project / "figures/plot.png", project / "c.drawio")
        export(project / "c.drawio", project / "c.svg", sign=False)
        assert [
            i.path for i in Store.load(project).get("c.drawio").inputs
        ] == ["figures/plot.png"]

    def test_a_missing_diagram_is_an_error(self, project: Path, monkeypatch):
        self.fake_drawio(project, monkeypatch)
        with pytest.raises(DrawioError, match="no such diagram"):
            export(project / "gone.drawio", project / "c.svg")

    def test_an_unsupported_format_is_refused(
        self, project: Path, monkeypatch
    ):
        self.fake_drawio(project, monkeypatch)
        import_image(project / "figures/plot.png", project / "c.drawio")
        with pytest.raises(DrawioError, match="cannot export"):
            export(project / "c.drawio", project / "c.tiff")

    def test_a_silent_failure_is_caught(self, project: Path, monkeypatch):
        """draw.io can exit zero having written nothing, so the file is the
        only reliable signal."""
        import subprocess as sp

        monkeypatch.setattr(
            "figmint.drawio.shutil.which", lambda n: "/fake/drawio"
        )
        monkeypatch.setattr(
            "figmint.drawio.subprocess.run",
            lambda c, **k: sp.CompletedProcess(c, 0, stdout="", stderr="nope"),
        )
        import_image(project / "figures/plot.png", project / "c.drawio")
        with pytest.raises(DrawioError, match="did not write"):
            export(project / "c.drawio", project / "c.svg")

    def test_a_missing_drawio_says_how_to_get_it(
        self, project: Path, monkeypatch
    ):
        monkeypatch.setattr("figmint.drawio.shutil.which", lambda name: None)
        import_image(project / "figures/plot.png", project / "c.drawio")
        with pytest.raises(DrawioError, match="not on PATH"):
            export(project / "c.drawio", project / "c.svg")

    def test_the_recorded_command_is_the_one_a_reader_could_run(
        self, project: Path, monkeypatch
    ):
        """A record that names something unrunnable is worse than one that names
        nothing: it reads as a reproduction recipe and is not one."""
        self.fake_drawio(project, monkeypatch)
        import_image(project / "figures/plot.png", project / "c.drawio")
        export(project / "c.drawio", project / "c.svg", sign=False)

        assert (
            Store.load(project).get("c.svg").command
            == "figmint drawio export c.drawio c.svg"
        )


class TestReimport:
    """Re-importing a regenerated panel refreshes it rather than duplicating it.

    When a figure is redrawn the diagram holds a stale copy of it, and the only
    sane repair is to replace that copy. Appending a second one would leave the
    old bytes on the canvas beside the new — wrong on the page and wrong in the
    record.
    """

    def test_the_same_source_is_replaced_not_appended(self, project: Path):
        import_image(project / "figures/plot.png", project / "c.drawio")
        _png(project / "figures" / "plot.png", size=(500, 250))
        import_image(project / "figures/plot.png", project / "c.drawio")

        assert len(Diagram.open(project / "c.drawio").embedded()) == 1

    def test_the_refreshed_bytes_are_the_new_ones(self, project: Path):
        import_image(project / "figures/plot.png", project / "c.drawio")
        _png(project / "figures" / "plot.png", size=(500, 250))
        import_image(project / "figures/plot.png", project / "c.drawio")

        source = (project / "figures/plot.png").read_bytes()
        assert embedded_bytes(project / "c.drawio") == source

    def test_re_importing_clears_the_staleness(self, project: Path):
        """The whole point: a redrawn panel should be repairable by re-running
        the same command, not by hand."""
        import_image(project / "figures/plot.png", project / "c.drawio")
        _png(project / "figures" / "plot.png", size=(500, 250))
        assert check_path(project / "c.drawio").state is State.STALE

        import_image(project / "figures/plot.png", project / "c.drawio")
        assert check_path(project / "c.drawio").state is State.OK

    def test_the_authors_layout_is_kept(self, project: Path):
        """Position is the author's decision; an import must not undo it."""
        import_image(
            project / "figures/plot.png", project / "c.drawio", x=123, y=456
        )
        _png(project / "figures" / "plot.png", size=(500, 250))
        import_image(project / "figures/plot.png", project / "c.drawio")

        text = (project / "c.drawio").read_text(encoding="utf-8")
        assert 'x="123" y="456"' in text

    def test_the_authors_sizing_is_kept(self, project: Path):
        """Laying out a composite *is* resizing its panels.

        Refreshing the picture inside one must not snap it back to the image's
        natural dimensions — that silently rearranges the whole page.
        """
        import_image(
            project / "figures/plot.png", project / "c.drawio", width=300
        )
        before = re.search(
            r'width="([\d.]+)" height="([\d.]+)"',
            (project / "c.drawio").read_text(encoding="utf-8"),
        ).groups()

        # Redrawn at a different natural size, which would otherwise win.
        _png(project / "figures" / "plot.png", size=(900, 300))
        import_image(project / "figures/plot.png", project / "c.drawio")

        after = re.search(
            r'width="([\d.]+)" height="([\d.]+)"',
            (project / "c.drawio").read_text(encoding="utf-8"),
        ).groups()
        assert after == before

    def test_an_explicit_size_still_wins(self, project: Path):
        import_image(
            project / "figures/plot.png", project / "c.drawio", width=300
        )
        import_image(
            project / "figures/plot.png", project / "c.drawio", width=500
        )
        width = re.search(
            r'width="([\d.]+)"',
            (project / "c.drawio").read_text(encoding="utf-8"),
        ).group(1)
        assert float(width) == 500

    def test_a_different_source_still_appends(self, project: Path):
        _png(project / "figures" / "other.png")
        import_image(project / "figures/plot.png", project / "c.drawio")
        import_image(project / "figures/other.png", project / "c.drawio")
        assert len(Diagram.open(project / "c.drawio").embedded()) == 2


class TestAutomaticRefresh:
    """Export re-embeds redrawn panels; nothing needs re-importing by hand.

    A diagram holds a *copy* of each panel, and the copy is what draw.io
    renders. Each shape already records the `src` it came from and the hash it
    had, so the diagram has everything needed to notice a figure has moved on —
    making the author run an import command to tell it so is busywork.
    """

    def test_a_redrawn_panel_is_re_embedded(self, project: Path, monkeypatch):
        seen = TestExport().fake_drawio(project, monkeypatch)
        import_image(project / "figures/plot.png", project / "c.drawio")
        _png(project / "figures" / "plot.png", size=(500, 250))

        result = export(project / "c.drawio", project / "c.svg", sign=False)
        assert result.refreshed == ["figures/plot.png"]
        assert (
            embedded_bytes(project / "c.drawio")
            == (project / "figures/plot.png").read_bytes()
        )
        del seen

    def test_the_refresh_happens_before_the_render(
        self, project: Path, monkeypatch
    ):
        """Refreshing afterwards would publish the old panels and only take
        effect on the next export."""
        captured: dict = {}

        def fake_run(command, **kwargs):
            import subprocess as sp

            captured["embedded"] = embedded_bytes(project / "c.drawio")
            Path(command[command.index("-o") + 1]).write_bytes(b"<svg/>")
            return sp.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(
            "figmint.drawio.shutil.which", lambda name: "/fake/drawio"
        )
        import_image(project / "figures/plot.png", project / "c.drawio")
        _png(project / "figures" / "plot.png", size=(500, 250))
        monkeypatch.setattr("figmint.drawio.subprocess.run", fake_run)

        export(project / "c.drawio", project / "c.svg", sign=False)
        assert (
            captured["embedded"] == (project / "figures/plot.png").read_bytes()
        )

    def test_it_clears_the_staleness(self, project: Path, monkeypatch):
        TestExport().fake_drawio(project, monkeypatch)
        import_image(project / "figures/plot.png", project / "c.drawio")
        _png(project / "figures" / "plot.png", size=(500, 250))
        assert check_path(project / "c.drawio").state is State.STALE

        export(project / "c.drawio", project / "c.svg", sign=False)
        assert check_path(project / "c.drawio").state is State.OK

    def test_an_untouched_panel_is_left_alone(
        self, project: Path, monkeypatch
    ):
        """No gratuitous rewriting of a file the author owns."""
        TestExport().fake_drawio(project, monkeypatch)
        import_image(project / "figures/plot.png", project / "c.drawio")
        before = (project / "c.drawio").read_bytes()

        result = export(project / "c.drawio", project / "c.svg", sign=False)
        assert result.refreshed == []
        assert (project / "c.drawio").read_bytes() == before

    def test_the_layout_survives_the_refresh(self, project: Path, monkeypatch):
        TestExport().fake_drawio(project, monkeypatch)
        import_image(
            project / "figures/plot.png", project / "c.drawio", x=123, y=456
        )
        _png(project / "figures" / "plot.png", size=(900, 300))

        export(project / "c.drawio", project / "c.svg", sign=False)
        assert 'x="123" y="456"' in (project / "c.drawio").read_text(
            encoding="utf-8"
        )

    def test_a_missing_panel_does_not_block_the_export(
        self, project: Path, monkeypatch
    ):
        """`figmint status` reports the missing input; failing here would block
        the one command that could still produce something useful."""
        TestExport().fake_drawio(project, monkeypatch)
        import_image(project / "figures/plot.png", project / "c.drawio")
        (project / "figures/plot.png").unlink()

        result = export(project / "c.drawio", project / "c.svg", sign=False)
        assert result.refreshed == []
