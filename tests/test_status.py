"""`figmint status` — the check the record exists to support.

Three failures are kept apart deliberately, because they have different fixes
and only one of them is ordinary:

  * an input changed — regenerate the artifact;
  * the artifact changed — something wrote it without going through figmint;
  * nothing is recorded at all.

None of them is repaired by editing `figmint.toml`, which is the one thing a
reader in a hurry might try.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from figmint.status import State, check_all, check_path
from figmint.store import Artifact, Input, Store, hash_file


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "data.csv").write_text("x,y\n1,2\n")
    (tmp_path / "uv.lock").write_text("lock")
    (tmp_path / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\nplot")

    store = Store.load(tmp_path)
    store.record(
        Artifact(
            path="plot.png",
            hash=hash_file(tmp_path / "plot.png"),
            command="uv run plot.py",
            inputs=[
                Input("data.csv", hash_file(tmp_path / "data.csv")),
                Input(
                    "uv.lock",
                    hash_file(tmp_path / "uv.lock"),
                    kind="environment",
                ),
            ],
        )
    )
    store.save()
    return tmp_path


class TestFreshness:
    def test_an_untouched_artifact_is_ok(self, project: Path):
        report = check_path(project / "plot.png")
        assert report.state is State.OK
        assert not report.stale

    def test_a_changed_input_makes_it_stale(self, project: Path):
        (project / "data.csv").write_text("x,y\n9,9\n")
        report = check_path(project / "plot.png")
        assert report.state is State.STALE
        assert [i.path for i in report.changed_inputs] == ["data.csv"]

    def test_a_changed_lock_makes_it_stale(self, project: Path):
        """The environment is an input; a different NumPy is a different figure."""
        (project / "uv.lock").write_text("different")
        report = check_path(project / "plot.png")
        assert report.state is State.STALE
        assert report.changed_inputs[0].kind == "environment"

    def test_a_missing_input_is_reported(self, project: Path):
        (project / "data.csv").unlink()
        report = check_path(project / "plot.png")
        assert report.state is State.STALE
        assert report.changed_inputs[0].state is State.MISSING

    def test_a_missing_artifact_is_reported(self, project: Path):
        (project / "plot.png").unlink()
        assert check_path(project / "plot.png").state is State.MISSING

    def test_an_edited_artifact_is_distinguished_from_a_stale_one(
        self, project: Path
    ):
        """Different failure, different fix: nothing upstream moved, so
        regenerating is not the answer — something wrote the file behind
        figmint's back."""
        (project / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\ntampered")
        report = check_path(project / "plot.png")
        assert report.state is State.MODIFIED
        assert "without going through figmint" in report.detail

    def test_stale_wins_over_modified(self, project: Path):
        """An artifact regenerated from changed inputs is stale, not modified.

        Reporting both would be noise, and `modified` would point at the wrong
        remedy.
        """
        (project / "data.csv").write_text("x,y\n9,9\n")
        (project / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\nnew")
        assert check_path(project / "plot.png").state is State.STALE

    def test_an_unrecorded_path_is_untracked(self, project: Path):
        (project / "other.png").write_bytes(b"x")
        report = check_path(project / "other.png")
        assert report.state is State.UNTRACKED
        assert "figmint run" in report.detail


class TestWholeProject:
    def test_every_artifact_is_checked(self, project: Path):
        store = Store.load(project)
        (project / "second.png").write_bytes(b"y")
        store.record(
            Artifact(path="second.png", hash=hash_file(project / "second.png"))
        )
        store.save()
        assert {r.path for r in check_all(project)} == {
            "plot.png",
            "second.png",
        }

    def test_results_are_sorted(self, project: Path):
        store = Store.load(project)
        for name in ("z.png", "a.png"):
            (project / name).write_bytes(b"x")
            store.record(Artifact(path=name, hash=hash_file(project / name)))
        store.save()
        assert [r.path for r in check_all(project)] == [
            "a.png",
            "plot.png",
            "z.png",
        ]

    def test_an_empty_project_is_not_an_error(self, tmp_path: Path):
        assert check_all(tmp_path) == []


class TestUnaccountedInputs:
    """The gap every other check is structurally unable to see.

    A figure can be current in every link and still rest on data nobody can
    place. Every check above it passes, which is exactly why it needs saying.
    """

    def test_an_undeclared_input_is_flagged(self, project: Path):
        report = check_path(project / "plot.png")
        assert [i.path for i in report.unaccounted_inputs] == ["data.csv"]
        # A warning, not a failure: the artifact itself is fine.
        assert report.state is State.OK

    def test_declaring_it_clears_the_flag(self, project: Path):
        from figmint.declare import declare
        from figmint.origins import attested

        declare(project / "data.csv", attested("A Researcher"))
        assert check_path(project / "plot.png").unaccounted_inputs == []

    def test_a_recorded_input_needs_no_declaration(self, project: Path):
        """Something figmint watched being made is already accounted for."""
        store = Store.load(project)
        store.record(
            Artifact(path="data.csv", hash=hash_file(project / "data.csv"))
        )
        store.save()
        assert check_path(project / "plot.png").unaccounted_inputs == []

    def test_a_lock_file_is_not_flagged(self, project: Path):
        """Demanding a declaration for uv.lock would be noise, and noise is how
        a real finding gets ignored."""
        assert "uv.lock" not in [
            i.path for i in check_path(project / "plot.png").unaccounted_inputs
        ]

    def test_an_undeclared_script_is_flagged(self, project: Path):
        """Code is an input like any other.

        A figure resting on a script nobody will claim is as unplaceable as one
        resting on data nobody collected — and a script a model wrote is exactly
        what a reader needs told.
        """
        store = Store.load(project)
        artifact = store.get("plot.png")
        artifact.inputs.append(Input("plot.py", "sha256:aa", kind="code"))
        store.record(artifact)
        store.save()
        (project / "plot.py").write_text("x")
        assert "plot.py" in [
            i.path for i in check_path(project / "plot.png").unaccounted_inputs
        ]

    def test_declaring_a_script_clears_it(self, project: Path):
        from figmint.declare import declare
        from figmint.origins import attested

        store = Store.load(project)
        (project / "plot.py").write_text("x")
        artifact = store.get("plot.png")
        artifact.inputs.append(
            Input("plot.py", hash_file(project / "plot.py"), kind="code")
        )
        store.record(artifact)
        store.save()

        declare(project / "plot.py", attested("A Researcher", "Claude Opus 5"))
        assert "plot.py" not in [
            i.path for i in check_path(project / "plot.png").unaccounted_inputs
        ]
