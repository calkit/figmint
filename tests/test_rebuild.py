"""`figmint rebuild` — run the repair sequence instead of printing it.

The record already holds the command that made each artifact and the inputs it
was made from, so the sequence is a property of the record. Printing it and
asking someone to retype it was a half-measure: a copied command can be
mistyped, and one wrong `-i` produces a record that is confidently false.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from figmint.rebuild import RebuildError, rebuild
from figmint.run import run
from figmint.status import State, check_all, check_path
from figmint.store import Artifact, Input, Store, hash_file


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "uv.lock").write_text("lock")
    (tmp_path / "data.csv").write_text("x,y\n1,2\n")
    (tmp_path / "make.py").write_text(
        "open('out.txt','w').write(open('data.csv').read())"
    )
    run(
        ["uv", "run", "--no-project", sys.executable, "make.py"],
        inputs=[tmp_path / "data.csv"],
        outputs=[tmp_path / "out.txt"],
        cwd=tmp_path,
    )
    return tmp_path


def chain(project: Path) -> None:
    """out.txt -> second.txt, so there is an order to get wrong."""
    (project / "make2.py").write_text(
        "open('second.txt','w').write(open('out.txt').read())"
    )
    run(
        ["uv", "run", "--no-project", sys.executable, "make2.py"],
        inputs=[project / "out.txt"],
        outputs=[project / "second.txt"],
        cwd=project,
    )


class TestRebuild:
    def test_a_clean_project_does_nothing(self, project: Path):
        assert rebuild(cwd=project).empty

    def test_it_repeats_the_recorded_command(self, project: Path):
        (project / "data.csv").write_text("x,y\n9,9\n")
        assert check_path(project / "out.txt").state is State.STALE

        result = rebuild(cwd=project)
        assert result.rebuilt == ["out.txt"]
        assert check_path(project / "out.txt").state is State.OK
        assert (project / "out.txt").read_text(
            encoding="utf-8"
        ) == "x,y\n9,9\n"

    def test_dependencies_come_first(self, project: Path):
        """Rebuilding `second.txt` before `out.txt` would build it from bytes
        that are about to change."""
        chain(project)
        (project / "data.csv").write_text("x,y\n9,9\n")

        result = rebuild(cwd=project, dry_run=True)
        assert result.rebuilt == ["out.txt", "second.txt"]

    def test_the_whole_chain_ends_up_current(self, project: Path):
        chain(project)
        (project / "data.csv").write_text("x,y\n9,9\n")

        rebuild(cwd=project)
        assert all(r.trustworthy for r in check_all(project))
        assert (project / "second.txt").read_text(
            encoding="utf-8"
        ) == "x,y\n9,9\n"

    def test_a_dry_run_changes_nothing(self, project: Path):
        (project / "data.csv").write_text("x,y\n9,9\n")
        before = (project / "out.txt").read_text(encoding="utf-8")

        rebuild(cwd=project, dry_run=True)
        assert (project / "out.txt").read_text(encoding="utf-8") == before

    def test_naming_a_path_includes_what_is_behind_it(self, project: Path):
        """Rebuilding a composite while its inputs are stale would produce a
        fresh artifact that is wrong."""
        chain(project)
        (project / "data.csv").write_text("x,y\n9,9\n")

        result = rebuild(["second.txt"], cwd=project, dry_run=True)
        assert result.rebuilt == ["out.txt", "second.txt"]

    def test_naming_a_path_excludes_what_is_not_behind_it(self, project: Path):
        chain(project)
        (project / "data.csv").write_text("x,y\n9,9\n")

        result = rebuild(["out.txt"], cwd=project, dry_run=True)
        assert result.rebuilt == ["out.txt"]

    def test_an_unrecorded_path_is_refused(self, project: Path):
        with pytest.raises(RebuildError, match="nothing recorded"):
            rebuild(["nowhere.txt"], cwd=project)

    def test_a_declared_file_is_skipped_with_a_reason(self, project: Path):
        """Nobody can regenerate raw data, so saying so beats a bare failure."""
        from figmint.declare import declare
        from figmint.origins import Author, attested

        (project / "notes.csv").write_text("collected by hand\n")
        declare(project / "notes.csv", attested(Author("A Researcher")))
        (project / "notes.csv").unlink()

        result = rebuild(cwd=project)
        assert [p for p, _ in result.skipped] == ["notes.csv"]
        assert "nothing produced it" in result.skipped[0][1]

    def test_a_hand_authored_diagram_is_skipped_with_a_reason(
        self, project: Path
    ):
        """Its export re-embeds the panels, so it needs no step of its own."""
        store = Store.load(project)
        (project / "c.drawio").write_text("<mxfile/>")
        store.record(
            Artifact(
                path="c.drawio",
                hash=hash_file(project / "c.drawio"),
                kind="authored",
                inputs=[Input("out.txt", hash_file(project / "out.txt"))],
            )
        )
        store.save()
        (project / "data.csv").write_text("x,y\n9,9\n")

        result = rebuild(cwd=project)
        assert ("c.drawio", "hand-authored — its export refreshes it") in (
            result.skipped
        )

    def test_an_untouched_artifact_is_left_alone(self, project: Path):
        """Re-running it would replace a perfectly good signature for nothing."""
        chain(project)
        (project / "out.txt").write_text("edited by hand")

        result = rebuild(cwd=project)
        assert "second.txt" in result.rebuilt
