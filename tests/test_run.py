"""`fromwhere run`, and the environment gate in front of it.

The refusal is the interesting part. fromwhere will not record an artifact
whose environment it cannot name, because "this came from that script and that
CSV" is a claim about two files — and the same script under a different NumPy
produces a different picture without either of them changing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fromwhere.environments import EnvironmentError_, describe
from fromwhere.run import RunError, run
from fromwhere.status import State, check_path
from fromwhere.store import Store, hash_file


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "uv.lock").write_text("lock")
    (tmp_path / "data.csv").write_text("x,y\n1,2\n")
    return tmp_path


class TestEnvironmentGate:
    def test_uv_run_resolves_the_lock(self, project: Path):
        env = describe(["uv", "run", "plot.py"], project)
        assert env.manager == "uv"
        assert env.lock == project / "uv.lock"

    def test_the_lock_is_found_from_a_subdirectory(self, project: Path):
        (project / "scripts").mkdir()
        assert describe(["uv", "run", "x.py"], project / "scripts").lock == (
            project / "uv.lock"
        )

    def test_a_bare_command_is_refused(self, project: Path):
        """The whole value of the record is that everything in it is checkable.

        Recording an artifact whose environment is unknown would put a claim in
        provenance.toml the file cannot support.
        """
        with pytest.raises(
            EnvironmentError_, match="not run through a recognized"
        ):
            describe(["python", "plot.py"], project)

    def test_uv_run_without_a_lock_is_refused(self, tmp_path: Path):
        with pytest.raises(EnvironmentError_, match="no uv.lock"):
            describe(["uv", "run", "plot.py"], tmp_path)

    @pytest.mark.parametrize(
        ("command", "lock", "manager"),
        [
            (["pixi", "run", "x"], "pixi.lock", "pixi"),
            (["bun", "run", "x"], "bun.lock", "bun"),
            (["cargo", "run"], "Cargo.lock", "cargo"),
            (["nix", "develop", "--command", "x"], "flake.lock", "nix"),
        ],
    )
    def test_each_manager_resolves_its_lock(
        self, project: Path, command: list[str], lock: str, manager: str
    ):
        (project / lock).write_text("pinned")
        env = describe(command, project)
        assert env.manager == manager
        assert env.lock == project / lock

    def test_a_manager_without_its_lock_is_refused(self, project: Path):
        with pytest.raises(EnvironmentError_, match="pixi install"):
            describe(["pixi", "run", "x"], project)

    def test_calkit_is_asked_where_the_lock_lives(
        self, project: Path, monkeypatch
    ):
        """Calkit fronts Docker, Conda, Julia and renv, each locking somewhere
        different. Guessing would mean reimplementing its resolution and then
        drifting from it, so the tool that owns the answer is asked."""
        (project / "env.lock").write_text("pinned")
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            import subprocess as sp

            return sp.CompletedProcess(
                command,
                0,
                stdout='{"kind": "conda", "lock_path": "env.lock"}',
                stderr="",
            )

        monkeypatch.setattr("fromwhere.environments.subprocess.run", fake_run)
        env = describe(
            ["calkit", "xenv", "-n", "main", "--", "python", "x.py"], project
        )
        assert env.manager == "calkit"
        assert env.lock == project / "env.lock"
        assert calls[0][:4] == ["calkit", "describe", "env", "-n"]
        # Asked for outright. Calkit's default output is YAML, which parses as
        # neither JSON nor an error, so without this the answer comes back
        # unreadable — and a mock that hands over JSON regardless would never
        # notice.
        assert "--json" in calls[0]

    @pytest.mark.parametrize(
        "command",
        [
            ["calkit", "nb", "exec", "-n", "main", "nb.ipynb"],
            ["calkit", "latex", "build", "-n", "tex", "paper.tex"],
            ["ck", "xenv", "-n", "main", "--", "true"],
        ],
    )
    def test_every_calkit_entry_point_is_recognized(
        self, project: Path, monkeypatch, command: list[str]
    ):
        (project / "env.lock").write_text("pinned")

        def fake_run(cmd, **kwargs):
            import subprocess as sp

            return sp.CompletedProcess(
                cmd,
                0,
                stdout='{"kind": "uv", "lock_path": "env.lock"}',
                stderr="",
            )

        monkeypatch.setattr("fromwhere.environments.subprocess.run", fake_run)
        assert describe(command, project).manager == "calkit"

    def test_a_calkit_command_needs_an_environment_name(self, project: Path):
        with pytest.raises(EnvironmentError_, match="-n <environment>"):
            describe(["calkit", "xenv", "--", "true"], project)

    def test_an_environment_with_no_lock_is_refused(
        self, project: Path, monkeypatch
    ):
        """A manager that pins nothing cannot back a claim about what ran."""

        def fake_run(cmd, **kwargs):
            import subprocess as sp

            return sp.CompletedProcess(
                cmd,
                0,
                stdout='{"kind": "docker", "lock_path": null}',
                stderr="",
            )

        monkeypatch.setattr("fromwhere.environments.subprocess.run", fake_run)
        with pytest.raises(EnvironmentError_, match="no lock file"):
            describe(["calkit", "xenv", "-n", "main", "--", "true"], project)

    def test_an_unsupported_calkit_subcommand_is_refused(self, project: Path):
        with pytest.raises(EnvironmentError_, match="managed environment"):
            describe(["calkit", "run"], project)


def _script(project: Path, body: str) -> None:
    (project / "make.py").write_text(body)


class TestRun:
    def command(self) -> list[str]:
        # `uv run` is the gate; the script itself runs through this interpreter
        # so the test does not need a resolved uv environment.
        return ["uv", "run", "--no-project", sys.executable, "make.py"]

    def test_it_records_inputs_outputs_and_the_environment(
        self, project: Path
    ):
        _script(project, "open('out.txt','w').write('hi')")
        result = run(
            self.command(),
            inputs=[project / "data.csv"],
            outputs=[project / "out.txt"],
            cwd=project,
        )
        artifact = result.artifacts[0]
        assert artifact.path == "out.txt"
        assert artifact.hash == hash_file(project / "out.txt")
        assert [(i.path, i.kind) for i in artifact.inputs] == [
            ("data.csv", "file"),
            ("uv.lock", "environment"),
            # Named by the command, so recorded without being asked for.
            ("make.py", "code"),
        ]

    def test_the_script_is_recorded_without_being_asked_for(
        self, project: Path
    ):
        """Forgetting `-i plot.py` is the easiest mistake to make and among the
        worst to live with: the script is the input most likely to change, and
        a record omitting it calls the figure current after the code that drew
        it was rewritten."""
        _script(project, "open('out.txt','w').write('hi')")
        result = run(
            self.command(),
            inputs=[],
            outputs=[project / "out.txt"],
            cwd=project,
        )
        assert ("make.py", "code") in [
            (i.path, i.kind) for i in result.artifacts[0].inputs
        ]

    def test_an_explicit_input_is_not_duplicated(self, project: Path):
        _script(project, "open('out.txt','w').write('hi')")
        result = run(
            self.command(),
            inputs=[project / "make.py"],
            outputs=[project / "out.txt"],
            cwd=project,
        )
        assert [i.path for i in result.artifacts[0].inputs].count(
            "make.py"
        ) == 1

    def test_files_outside_the_project_are_not_inferred(self, project: Path):
        """The interpreter is not part of the analysis, and a record that
        hashes it would go stale on every unrelated Python upgrade."""
        _script(project, "open('out.txt','w').write('hi')")
        result = run(
            self.command(),
            inputs=[],
            outputs=[project / "out.txt"],
            cwd=project,
        )
        # `is_absolute` rather than a leading-slash check: on Windows an
        # absolute path is `C:/...`, so the slash test would pass here without
        # testing anything.
        assert all(
            not Path(i.path).is_absolute() for i in result.artifacts[0].inputs
        )

    def test_a_word_that_is_not_a_file_is_ignored(self, project: Path):
        """Kept narrow so a stray token cannot become a phantom input."""
        _script(project, "open('out.txt','w').write('hi')")
        command = self.command() + ["--label", "not-a-file"]
        result = run(
            command, inputs=[], outputs=[project / "out.txt"], cwd=project
        )
        assert "not-a-file" not in [i.path for i in result.artifacts[0].inputs]

    def test_the_record_lands_in_fromwhere_toml(self, project: Path):
        _script(project, "open('out.txt','w').write('hi')")
        run(
            self.command(),
            inputs=[],
            outputs=[project / "out.txt"],
            cwd=project,
        )
        assert Store.load(project).get("out.txt") is not None

    def test_inputs_are_hashed_before_the_command_runs(self, project: Path):
        """A script that rewrites its own input would otherwise be recorded
        against the version it produced rather than the one it read."""
        before = hash_file(project / "data.csv")
        _script(
            project,
            "open('data.csv','w').write('x,y\\n9,9\\n')\n"
            "open('out.txt','w').write('hi')",
        )
        result = run(
            self.command(),
            inputs=[project / "data.csv"],
            outputs=[project / "out.txt"],
            cwd=project,
        )
        recorded = next(
            i for i in result.artifacts[0].inputs if i.path == "data.csv"
        )
        assert recorded.hash == before
        assert hash_file(project / "data.csv") != before

    def test_a_failing_command_records_nothing(self, project: Path):
        """A partially written output with a hash beside it is worse than no
        record at all."""
        _script(project, "raise SystemExit(3)")
        with pytest.raises(RunError, match="exit code"):
            run(
                self.command(),
                inputs=[],
                outputs=[project / "out.txt"],
                cwd=project,
            )
        assert Store.load(project).artifacts == {}

    def test_a_missing_output_is_an_error(self, project: Path):
        _script(project, "pass")
        with pytest.raises(RunError, match="did not produce"):
            run(
                self.command(),
                inputs=[],
                outputs=[project / "out.txt"],
                cwd=project,
            )

    def test_a_missing_input_is_an_error(self, project: Path):
        _script(project, "open('out.txt','w').write('hi')")
        with pytest.raises(RunError, match="input does not exist"):
            run(
                self.command(),
                inputs=[project / "nope.csv"],
                outputs=[project / "out.txt"],
                cwd=project,
            )

    def test_output_directories_are_created(self, project: Path):
        _script(project, "open('figures/out.txt','w').write('hi')")
        run(
            self.command(),
            inputs=[],
            outputs=[project / "figures" / "out.txt"],
            cwd=project,
        )
        assert (project / "figures" / "out.txt").is_file()

    def test_an_output_is_required(self, project: Path):
        with pytest.raises(RunError, match="output is required"):
            run(self.command(), inputs=[], outputs=[], cwd=project)


class TestDeclaredInputRefresh:
    """Editing a declared source file should not require re-declaring it.

    A declaration answers "who is responsible for this file", and that does not
    change when somebody edits a line of it. Before this, every edit left the
    file sitting in `fromwhere status` as *modified* until it was declared again —
    a treadmill that teaches people to re-run `declare` reflexively, which is
    the last habit this tool should build.
    """

    def command(self) -> list[str]:
        return ["uv", "run", "--no-project", sys.executable, "make.py"]

    def declared_script(self, project: Path) -> None:
        from fromwhere.declare import declare
        from fromwhere.origins import Author, attested

        _script(project, "open('out.txt','w').write('one')")
        declare(project / "make.py", attested(Author("A Researcher")))

    def go(self, project: Path, inputs=None, output="out.txt"):
        return run(
            self.command(),
            inputs=inputs or [project / "data.csv"],
            outputs=[project / output],
            cwd=project,
        )

    def test_a_run_brings_the_recorded_hash_up_to_date(self, project: Path):
        self.declared_script(project)
        _script(project, "open('out.txt','w').write('edited')")

        result = self.go(project)
        assert "make.py" in result.refreshed
        assert check_path(project / "make.py").state is State.OK

    def test_the_authorship_survives_the_refresh(self, project: Path):
        """The claim is about a person, not about bytes."""
        self.declared_script(project)
        _script(project, "open('out.txt','w').write('edited')")
        self.go(project)

        artifact = Store.load(project).get("make.py")
        assert [a.name for a in artifact.authors] == ["A Researcher"]
        assert artifact.origin_kind == "attested"

    def test_an_unchanged_input_is_not_reported_as_refreshed(
        self, project: Path
    ):
        self.declared_script(project)
        assert "make.py" not in self.go(project).refreshed

    def test_a_produced_artifact_is_never_refreshed(self, project: Path):
        """The line that matters.

        A hash fromwhere wrote itself is evidence. Quietly rewriting it is exactly
        the tampering the record exists to catch, so only declarations — which
        nothing produced — are eligible.
        """
        _script(project, "open('out.txt','w').write('one')")
        self.go(project)
        recorded = Store.load(project).get("out.txt").hash

        (project / "out.txt").write_text("tampered with")
        assert check_path(project / "out.txt").state is State.MODIFIED

        # A later run that *uses* it must not launder the tampering.
        _script(project, "open('second.txt','w').write('two')")
        result = self.go(
            project, inputs=[project / "out.txt"], output="second.txt"
        )
        assert "out.txt" not in result.refreshed
        after = Store.load(project).get("out.txt").hash
        assert after == recorded
        assert after != hash_file(project / "out.txt")


class TestRefreshTiming:
    """The refresh has to land before the command, not after.

    A document build renders provenance panels out of `provenance.toml`. If the
    refresh happened afterwards, the page produced by that very run would report
    its own sources as edited, and only a *second* build would clear it — which
    is exactly the sort of "run it twice" behavior nobody ever discovers.
    """

    def command(self) -> list[str]:
        return ["uv", "run", "--no-project", sys.executable, "make.py"]

    def test_the_command_sees_the_refreshed_record(self, project: Path):
        from fromwhere.declare import declare
        from fromwhere.origins import Author, attested

        _script(project, "x = 1")
        declare(project / "make.py", attested(Author("A Researcher")))

        # Edit it, then have the run itself read the record back out — standing
        # in for a document build rendering a provenance panel.
        _script(
            project,
            "import shutil;"
            "shutil.copy('provenance.toml','seen.toml');"
            "open('out.txt','w').write('done')",
        )
        run(
            self.command(),
            inputs=[project / "data.csv"],
            outputs=[project / "out.txt"],
            cwd=project,
        )

        seen = (project / "seen.toml").read_text(encoding="utf-8")
        assert hash_file(project / "make.py") in seen

    def test_a_failed_command_still_leaves_the_record_readable(
        self, project: Path
    ):
        """The refresh describes the input bytes, which is true either way."""
        from fromwhere.declare import declare
        from fromwhere.origins import Author, attested

        _script(project, "x = 1")
        declare(project / "make.py", attested(Author("A Researcher")))
        _script(project, "raise SystemExit(3)")

        with pytest.raises(RunError):
            run(
                self.command(),
                inputs=[project / "data.csv"],
                outputs=[project / "out.txt"],
                cwd=project,
            )
        # Still a valid record naming the same author.
        artifact = Store.load(project).get("make.py")
        assert [a.name for a in artifact.authors] == ["A Researcher"]
