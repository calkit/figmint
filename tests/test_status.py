"""Tests for staleness checking and the CLI's exit contract."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from figmint.assets import hash_file
from figmint.cli import EXIT_ERROR, EXIT_OK, EXIT_STALE, main
from figmint.document import load
from figmint.status import SourceState, check

EXAMPLES = Path(__file__).parent.parent / "examples"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """An isolated copy of the example project, safe to mutate."""
    shutil.copy(EXAMPLES / "two-panel.fig.yaml", tmp_path / "two-panel.fig.yaml")
    shutil.copytree(EXAMPLES / "figures", tmp_path / "figures")
    return tmp_path


class TestStatus:
    def test_pristine_project_is_up_to_date(self, project: Path):
        report = check(load(project / "two-panel.fig.yaml"))
        assert not report.stale
        assert all(s.state is SourceState.OK for s in report.sources)

    def test_edited_component_is_stale(self, project: Path):
        target = project / "figures" / "cp_curve.svg"
        target.write_text(target.read_text() + "\n<!-- edited -->")

        report = check(load(project / "two-panel.fig.yaml"))
        assert report.stale
        stale = [s for s in report.sources if s.state is SourceState.STALE]
        assert [s.key for s in stale] == ["cp-curve"]

    def test_deleted_component_is_missing(self, project: Path):
        (project / "figures" / "wake_profile.svg").unlink()
        report = check(load(project / "two-panel.fig.yaml"))
        assert report.stale
        assert any(s.state is SourceState.MISSING for s in report.sources)

    def test_source_without_a_recorded_hash_is_unknown(self, project: Path):
        doc = load(project / "two-panel.fig.yaml")
        del doc.sources["cp-curve"]["hash"]
        report = check(doc)
        states = {s.key: s.state for s in report.sources}
        assert states["cp-curve"] is SourceState.UNKNOWN
        # Unknown is not a failure — hand-written YAML is legitimate.
        assert not report.stale

    def test_node_referencing_an_undefined_source_is_reported(self, project: Path):
        doc = load(project / "two-panel.fig.yaml")
        doc.nodes[0]["source"] = "nonexistent-key"
        report = check(doc)
        assert report.stale
        assert any(s.key == "nonexistent-key" for s in report.sources)

    def test_signing_a_component_does_not_make_it_stale(self, project: Path):
        # The signed fixture is the same artwork with a manifest embedded, so
        # its bytes differ but its recorded content digest does not.
        doc_path = project / "two-panel.fig.yaml"
        signed = FIXTURES / "signed_by_stencila.svg"
        shutil.copy(signed, project / "figures" / "cp_curve.svg")

        # Sanity: the file really did change on disk.
        assert hash_file(project / "figures" / "cp_curve.svg") != load(
            doc_path
        ).sources["cp-curve"]["hash"]

        report = check(load(doc_path))
        cp = next(s for s in report.sources if s.key == "cp-curve")
        assert cp.state is SourceState.OK
        assert "signed" in cp.detail

    def test_output_older_than_inputs_is_flagged(self, project: Path):
        import os
        import time

        output = project / "two-panel.svg"
        output.write_text("<svg/>")
        # Backdate the output well behind its inputs.
        old = time.time() - 3600
        os.utime(output, (old, old))

        report = check(load(project / "two-panel.fig.yaml"))
        assert report.stale
        assert output in report.outdated_outputs

    def test_fresh_output_is_not_flagged(self, project: Path):
        output = project / "two-panel.svg"
        output.write_text("<svg/>")  # written now, so newer than every input
        report = check(load(project / "two-panel.fig.yaml"))
        assert report.outdated_outputs == []
        assert not report.stale


class TestCliExitCodes:
    """The exit contract is the whole point of `status` for CI and agents."""

    def test_clean_project_exits_zero(self, project: Path, capsys):
        assert main(["status", str(project)]) == EXIT_OK
        assert "up to date" in capsys.readouterr().out

    def test_stale_project_exits_one(self, project: Path, capsys):
        target = project / "figures" / "cp_curve.svg"
        target.write_text(target.read_text() + "\n<!-- edited -->")
        assert main(["status", str(project)]) == EXIT_STALE
        assert "STALE" in capsys.readouterr().out

    def test_malformed_document_exits_two(self, tmp_path: Path, capsys):
        (tmp_path / "broken.fig.yaml").write_text("just: a mapping\n")
        assert main(["status", str(tmp_path)]) == EXIT_ERROR
        assert "figmint" in capsys.readouterr().err

    def test_no_documents_found_is_an_error(self, tmp_path: Path):
        assert main(["status", str(tmp_path)]) == EXIT_ERROR

    def test_verbose_lists_healthy_components(self, project: Path, capsys):
        main(["status", str(project), "-v"])
        out = capsys.readouterr().out
        assert "cp-curve" in out
        assert "wake-profile" in out

    def test_quiet_by_default_hides_healthy_components(self, project: Path, capsys):
        main(["status", str(project)])
        assert "cp-curve" not in capsys.readouterr().out


class TestBuildCli:
    def test_build_writes_an_svg(self, project: Path, capsys):
        assert main(["build", str(project / "two-panel.fig.yaml")]) == EXIT_OK
        assert (project / "two-panel.svg").exists()
        assert "built" in capsys.readouterr().out

    def test_if_stale_skips_a_current_figure(self, project: Path, capsys):
        main(["build", str(project / "two-panel.fig.yaml")])
        capsys.readouterr()

        assert (
            main(["build", str(project / "two-panel.fig.yaml"), "--if-stale"])
            == EXIT_OK
        )
        assert "skipping" in capsys.readouterr().out

    def test_if_stale_rebuilds_after_a_component_changes(
        self, project: Path, capsys
    ):
        main(["build", str(project / "two-panel.fig.yaml")])
        capsys.readouterr()

        target = project / "figures" / "cp_curve.svg"
        target.write_text(target.read_text() + "\n<!-- edited -->")

        main(["build", str(project / "two-panel.fig.yaml"), "--if-stale"])
        assert "built" in capsys.readouterr().out

    def test_build_reports_warnings_without_failing(self, project: Path, capsys):
        doc = project / "two-panel.fig.yaml"
        doc.write_text(doc.read_text().replace("figures/cp_curve.svg", "figures/gone.svg"))
        assert main(["build", str(doc)]) == EXIT_OK
        assert "warning" in capsys.readouterr().err
