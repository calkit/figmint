"""`figmint run`, and the environment gate in front of it.

The refusal is the interesting part. figmint will not record an artifact whose
environment it cannot name, because "this came from that script and that CSV" is
a claim about two files — and the same script under a different NumPy produces a
different picture without either of them changing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from figmint.environments import EnvironmentError_, describe
from figmint.run import RunError, run
from figmint.store import Store, hash_file


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
        figmint.toml the file cannot support.
        """
        with pytest.raises(
            EnvironmentError_, match="not run through a recognised"
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

        monkeypatch.setattr("figmint.environments.subprocess.run", fake_run)
        env = describe(
            ["calkit", "xenv", "-n", "main", "--", "python", "x.py"], project
        )
        assert env.manager == "calkit"
        assert env.lock == project / "env.lock"
        assert calls[0][:4] == ["calkit", "describe", "env", "-n"]

    @pytest.mark.parametrize(
        "command",
        [
            ["calkit", "nb", "exec", "-n", "main", "nb.ipynb"],
            ["calkit", "latex", "build", "-n", "tex", "paper.tex"],
            ["ck", "xenv", "-n", "main", "--", "true"],
        ],
    )
    def test_every_calkit_entry_point_is_recognised(
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

        monkeypatch.setattr("figmint.environments.subprocess.run", fake_run)
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

        monkeypatch.setattr("figmint.environments.subprocess.run", fake_run)
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
        assert all(
            not i.path.startswith("/") for i in result.artifacts[0].inputs
        )

    def test_a_word_that_is_not_a_file_is_ignored(self, project: Path):
        """Kept narrow so a stray token cannot become a phantom input."""
        _script(project, "open('out.txt','w').write('hi')")
        command = self.command() + ["--label", "not-a-file"]
        result = run(
            command, inputs=[], outputs=[project / "out.txt"], cwd=project
        )
        assert "not-a-file" not in [i.path for i in result.artifacts[0].inputs]

    def test_the_record_lands_in_figmint_toml(self, project: Path):
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
