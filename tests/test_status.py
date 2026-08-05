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
        from figmint.origins import Author, attested

        declare(project / "data.csv", attested(Author("A Researcher")))
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
        from figmint.origins import Author, attested

        store = Store.load(project)
        (project / "plot.py").write_text("x")
        artifact = store.get("plot.png")
        artifact.inputs.append(
            Input("plot.py", hash_file(project / "plot.py"), kind="code")
        )
        store.record(artifact)
        store.save()

        declare(
            project / "plot.py",
            attested(Author("A Researcher"), Author("Claude Opus 5", "ai")),
        )
        assert "plot.py" not in [
            i.path for i in check_path(project / "plot.png").unaccounted_inputs
        ]


class TestUpstreamTrouble:
    """Tamper detection has to survive the links above it.

    An artifact can be sound in every direct link and still rest on a file that
    was tampered with, because nothing in between was regenerated. Every check
    passes, which is exactly why it needs saying.
    """

    def build_chain(self, project: Path) -> None:
        """plot.png -> composite -> final. Only plot.png names data.csv."""
        store = Store.load(project)
        for name, source in (
            ("composite", "plot.png"),
            ("final", "composite"),
        ):
            (project / name).write_text(name)
            store.record(
                Artifact(
                    path=name,
                    hash=hash_file(project / name),
                    inputs=[Input(source, hash_file(project / source))],
                )
            )
            store.save()

    def test_a_clean_chain_reports_nothing(self, project: Path):
        self.build_chain(project)
        assert check_path(project / "final").upstream == []
        assert check_path(project / "final").trustworthy

    def test_a_tampered_output_is_seen_from_the_far_end(self, project: Path):
        """The direct input `composite` is untouched, so every link above
        plot.png passes on its own."""
        self.build_chain(project)
        (project / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\ntampered")

        report = check_path(project / "final")
        # `final` names only `composite`, whose bytes never moved, so nothing
        # about `final` itself is wrong.
        assert report.state is State.OK
        assert report.inputs[0].state is State.OK
        # But the tampering is still visible from here.
        assert "plot.png" in report.upstream
        assert not report.trustworthy

    def test_the_whole_project_view_agrees(self, project: Path):
        self.build_chain(project)
        (project / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\ntampered")
        by_path = {r.path: r for r in check_all(project)}
        assert "plot.png" in by_path["final"].upstream
        assert by_path["plot.png"].state is State.MODIFIED

    def test_an_artifact_is_not_its_own_upstream(self, project: Path):
        (project / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\ntampered")
        assert check_path(project / "plot.png").upstream == []


class TestRebuildPlan:
    """ "Regenerate the stale artifacts" is true and useless.

    It does not say which command, and rebuilding a document before the figure
    it embeds accomplishes nothing — so someone runs everything twice before
    noticing. Everything needed to answer properly is already in the record.
    """

    def chain(self, project: Path) -> None:
        from figmint.store import Store

        store = Store.load(project)
        (project / "composite.svg").write_text("<svg/>")
        store.record(
            Artifact(
                path="composite.svg",
                hash=hash_file(project / "composite.svg"),
                command="figmint drawio export c.drawio composite.svg",
                inputs=[Input("plot.png", hash_file(project / "plot.png"))],
            )
        )
        store.save()

    def plan(self, project: Path) -> list[str]:
        from figmint.status import project_store, rebuild_plan

        return rebuild_plan(project_store(project), check_all(project))

    def test_a_clean_project_needs_nothing(self, project: Path):
        assert self.plan(project) == []

    def test_it_reconstructs_a_full_figmint_invocation(self, project: Path):
        """Not the bare recorded command: re-running that directly would
        produce the file *outside* figmint, and the record would then call it
        modified — turning "stale" into "tampered with"."""
        (project / "data.csv").write_text("x,y\n9,9\n")
        assert self.plan(project) == [
            "figmint run -i data.csv -o plot.png -- uv run plot.py"
        ]

    def test_the_lock_is_not_repeated_as_an_input(self, project: Path):
        """It is discovered from the command, not passed in."""
        (project / "data.csv").write_text("x,y\n9,9\n")
        assert "uv.lock" not in self.plan(project)[0]

    def test_dependencies_come_first(self, project: Path):
        """Alphabetical would put composite.svg before plot.png and have the
        user rebuild the composite from a figure that had not been redrawn."""
        self.chain(project)
        (project / "data.csv").write_text("x,y\n9,9\n")

        plan = self.plan(project)
        assert plan.index(
            "figmint run -i data.csv -o plot.png -- uv run plot.py"
        ) < plan.index("figmint drawio export c.drawio composite.svg")

    def test_an_existing_figmint_command_is_passed_through(
        self, project: Path
    ):
        self.chain(project)
        (project / "data.csv").write_text("x,y\n9,9\n")
        assert "figmint drawio export c.drawio composite.svg" in self.plan(
            project
        )

    def test_a_hand_authored_diagram_needs_no_step_of_its_own(
        self, project: Path
    ):
        """Exporting re-embeds any panel that has been redrawn, so the export
        already in the plan covers it. Naming an import here would be busywork
        the tool has stopped needing."""
        from figmint.store import Store

        store = Store.load(project)
        (project / "c.drawio").write_text("<mxfile/>")
        store.record(
            Artifact(
                path="c.drawio",
                hash=hash_file(project / "c.drawio"),
                kind="authored",
                inputs=[Input("plot.png", hash_file(project / "plot.png"))],
            )
        )
        store.save()
        (project / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\nredrawn")

        assert not any("drawio import" in c for c in self.plan(project))

    def test_nothing_is_suggested_for_a_declared_file(self, project: Path):
        """Nobody can regenerate raw data; the remedy is elsewhere."""
        from figmint.declare import declare
        from figmint.origins import Author, attested

        declare(project / "data.csv", attested(Author("A Researcher")))
        (project / "data.csv").write_text("x,y\n9,9\n")
        assert not any("data.csv -o data.csv" in c for c in self.plan(project))


class TestDeclaredArtifacts:
    """A declaration is not a claim about bytes.

    It says who is answerable for a file. Nothing produced a declared artifact,
    so its content changing means a person edited it — and what matters about
    that is whether the edit reached an output, which the input hashes on each
    output already record.
    """

    def declare_data(self, project: Path) -> None:
        from figmint.declare import declare
        from figmint.origins import Author, attested

        declare(project / "data.csv", attested(Author("A Researcher")))

    def test_editing_it_is_not_a_finding(self, project: Path):
        self.declare_data(project)
        (project / "data.csv").write_text("x,y\n9,9\n")

        report = check_path(project / "data.csv")
        assert report.state is State.OK
        assert not report.stale
        assert report.trustworthy

    def test_what_was_built_from_it_still_goes_stale(self, project: Path):
        """The edit is reported where it actually matters."""
        self.declare_data(project)
        (project / "data.csv").write_text("x,y\n9,9\n")
        assert check_path(project / "plot.png").state is State.STALE

    def test_a_deleted_declaration_is_still_reported(self, project: Path):
        """Not checking the hash is not the same as not checking at all."""
        self.declare_data(project)
        (project / "data.csv").unlink()
        assert check_path(project / "data.csv").state is State.MISSING

    def test_a_produced_artifact_is_still_checked(self, project: Path):
        """The tampering check is untouched — that hash is evidence."""
        (project / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\ntampered")
        report = check_path(project / "plot.png")
        assert report.state is State.MODIFIED
        assert "without going through figmint" in report.detail
