"""Tests for accepting changed components.

Accepting rewrites the document, so most of these are really about *not*
damaging a file people are expected to read and hand-edit.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml as pyyaml

from figmint.accept import accept
from figmint.assets import hash_file
from figmint.cli import EXIT_OK, main
from figmint.document import load
from figmint.status import SourceState, check

EXAMPLES = Path(__file__).parent.parent / "examples"
ORIGINAL = EXAMPLES / "two-panel.fig.yaml"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copy(ORIGINAL, tmp_path / "two-panel.fig.yaml")
    shutil.copytree(EXAMPLES / "figures", tmp_path / "figures")
    return tmp_path


@pytest.fixture
def changed(project: Path) -> Path:
    """A project whose `cp-curve` component has been regenerated."""
    target = project / "figures" / "cp_curve.svg"
    target.write_text(target.read_text() + "\n<!-- regenerated -->")
    return project


class TestAccept:
    def test_records_the_new_hash(self, changed: Path):
        doc_path = changed / "two-panel.fig.yaml"
        accepted = accept(load(doc_path))
        assert [a.key for a in accepted] == ["cp-curve"]

        reloaded = load(doc_path)
        assert reloaded.sources["cp-curve"]["hash"] == hash_file(
            changed / "figures" / "cp_curve.svg"
        )

    def test_clears_the_stale_warning(self, changed: Path):
        doc_path = changed / "two-panel.fig.yaml"
        assert check(load(doc_path)).stale
        accept(load(doc_path))
        assert not check(load(doc_path)).stale

    def test_untouched_components_are_left_alone(self, changed: Path):
        doc_path = changed / "two-panel.fig.yaml"
        before = load(doc_path).sources["wake-profile"]["hash"]
        accept(load(doc_path))
        assert load(doc_path).sources["wake-profile"]["hash"] == before

    def test_accepting_a_clean_project_changes_nothing(self, project: Path):
        doc_path = project / "two-panel.fig.yaml"
        before = doc_path.read_text()
        assert accept(load(doc_path)) == []
        assert doc_path.read_text() == before

    def test_can_accept_a_single_source(self, project: Path):
        for name in ("cp_curve.svg", "wake_profile.svg"):
            target = project / "figures" / name
            target.write_text(target.read_text() + "\n<!-- edited -->")

        doc_path = project / "two-panel.fig.yaml"
        accepted = accept(load(doc_path), keys=["cp-curve"])
        assert [a.key for a in accepted] == ["cp-curve"]

        # The other one is still flagged, so the warning is not lost.
        report = check(load(doc_path))
        states = {s.key: s.state for s in report.sources}
        assert states["wake-profile"] is SourceState.STALE

    def test_missing_components_cannot_be_accepted(self, project: Path):
        (project / "figures" / "cp_curve.svg").unlink()
        doc_path = project / "two-panel.fig.yaml"
        assert accept(load(doc_path)) == []
        # Still reported, because deleting a file is not something you accept.
        assert check(load(doc_path)).stale

    def test_records_when_it_was_accepted(self, changed: Path):
        doc_path = changed / "two-panel.fig.yaml"
        accept(load(doc_path))
        provenance = load(doc_path).sources["cp-curve"]["provenance"]
        assert "acceptedAt" in provenance

    def test_drops_credentials_when_a_component_loses_its_manifest(
        self, project: Path
    ):
        doc_path = project / "two-panel.fig.yaml"
        # Claim credentials the file does not actually have.
        data = pyyaml.safe_load(doc_path.read_text())
        data["sources"]["cp-curve"]["credentials"] = {"validationState": "Trusted"}
        doc_path.write_text(pyyaml.safe_dump(data))

        target = project / "figures" / "cp_curve.svg"
        target.write_text(target.read_text() + "\n<!-- edited -->")

        accept(load(doc_path))
        # Keeping the old claim would leave the document asserting provenance
        # the artifact no longer has.
        assert "credentials" not in load(doc_path).sources["cp-curve"]


class TestFilePreservation:
    """The format promises to be human-editable; rewriting must respect that."""

    def test_output_is_still_valid_yaml(self, changed: Path):
        doc_path = changed / "two-panel.fig.yaml"
        accept(load(doc_path))
        # Parse with a plain loader, not ruamel, to catch indentation damage.
        parsed = pyyaml.safe_load(doc_path.read_text())
        assert parsed["id"] == "fig-turbine-performance"
        assert len(parsed["nodes"]) == 6

    def test_comments_survive(self, changed: Path):
        doc_path = changed / "two-panel.fig.yaml"
        accept(load(doc_path))
        text = doc_path.read_text()
        assert "# A figmint document." in text
        assert "# Paint order: later entries draw on top." in text

    def test_explicit_nulls_are_preserved(self, changed: Path):
        doc_path = changed / "two-panel.fig.yaml"
        accept(load(doc_path))
        text = doc_path.read_text()
        # `background:` with an empty value parses the same but reads like a
        # mistake in a file people review.
        assert "background: null" in text
        assert "background:\n" not in text

    def test_whole_numbers_do_not_become_floats(self, changed: Path):
        doc_path = changed / "two-panel.fig.yaml"
        accept(load(doc_path))
        text = doc_path.read_text()
        assert "width: 216" in text
        assert "216.0" not in text

    def test_the_diff_is_small(self, changed: Path):
        import difflib

        doc_path = changed / "two-panel.fig.yaml"
        before = ORIGINAL.read_text().splitlines()
        accept(load(doc_path))
        after = doc_path.read_text().splitlines()

        # A real diff, not a positional comparison — accepting inserts lines,
        # which would make every subsequent line look changed.
        edits = [
            line
            for line in difflib.unified_diff(before, after, n=0)
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        ]
        removed = [line for line in edits if line.startswith("-")]
        added = [line for line in edits if line.startswith("+")]

        # Exactly one line replaced (the hash) plus three recorded facts.
        assert len(removed) == 1, f"unexpected removals: {removed}"
        assert "hash:" in removed[0]
        assert len(added) == 4, f"unexpected additions: {added}"

    def test_node_geometry_is_untouched(self, changed: Path):
        doc_path = changed / "two-panel.fig.yaml"
        before = load(doc_path).nodes
        accept(load(doc_path))
        assert load(doc_path).nodes == before


class TestAcceptCli:
    def test_accept_command_clears_staleness(self, changed: Path, capsys):
        assert main(["accept", str(changed)]) == EXIT_OK
        assert "accepted cp-curve" in capsys.readouterr().out
        assert not check(load(changed / "two-panel.fig.yaml")).stale

    def test_reports_when_there_is_nothing_to_do(self, project: Path, capsys):
        assert main(["accept", str(project)]) == EXIT_OK
        assert "nothing to accept" in capsys.readouterr().out

    def test_source_filter_is_honoured(self, project: Path, capsys):
        for name in ("cp_curve.svg", "wake_profile.svg"):
            target = project / "figures" / name
            target.write_text(target.read_text() + "\n<!-- edited -->")

        main(["accept", str(project), "--source", "cp-curve"])
        out = capsys.readouterr().out
        assert "cp-curve" in out
        assert "wake-profile" not in out


class TestBuildIfStale:
    def test_builds_when_no_output_exists_yet(self, project: Path, capsys):
        # A figure that has never been built is not "stale" — there is nothing
        # for it to be stale relative to — but it still needs building.
        assert main(["build", str(project), "--if-stale"]) == EXIT_OK
        assert "built" in capsys.readouterr().out
        assert (project / "two-panel.svg").exists()

    def test_skips_once_the_output_is_current(self, project: Path, capsys):
        main(["build", str(project), "--if-stale"])
        capsys.readouterr()
        main(["build", str(project), "--if-stale"])
        assert "skipping" in capsys.readouterr().out

    def test_builds_a_missing_format_even_when_svg_exists(
        self, project: Path, capsys
    ):
        main(["build", str(project), "--to", "svg"])
        capsys.readouterr()
        # PNG has never been produced, so --if-stale must not skip.
        assert _needs_png(project)

    def test_full_loop_ends_clean(self, project: Path):
        target = project / "figures" / "cp_curve.svg"
        target.write_text(target.read_text() + "\n<!-- regenerated -->")

        main(["accept", str(project)])
        main(["build", str(project), "--if-stale"])

        report = check(load(project / "two-panel.fig.yaml"))
        assert not report.stale
        assert (project / "two-panel.svg").exists()


def _needs_png(project: Path) -> bool:
    from figmint.cli import _needs_build

    return _needs_build(
        load(project / "two-panel.fig.yaml"), ("png",), None
    )
