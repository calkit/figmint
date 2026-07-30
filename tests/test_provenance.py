"""Tests for provenance levels, the project policy, and the Calkit boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from figmint import calkit
from figmint.cli import EXIT_ERROR, EXIT_OK, EXIT_STALE, main
from figmint.importer import ImportError_, Origin, declare
from figmint.provenance import (
    DEFAULT_POLICY,
    Level,
    Policy,
    assess,
    load_policy,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestLevels:
    def test_levels_are_ordered_worst_to_best(self):
        assert (
            Level.UNIDENTIFIED
            < Level.DECLARED
            < Level.GENERATED
            < Level.REPRODUCIBLE
            < Level.SIGNED
        )

    def test_parse_is_case_insensitive(self):
        assert Level.parse("SIGNED") is Level.SIGNED
        assert Level.parse(" reproducible ") is Level.REPRODUCIBLE

    def test_unknown_level_names_are_rejected(self):
        with pytest.raises(ValueError, match="unknown provenance level"):
            Level.parse("probably-fine")


class TestAssess:
    def test_nothing_known_is_unidentified(self):
        assert assess({}, {}).level is Level.UNIDENTIFIED

    def test_a_stated_origin_is_declared(self):
        result = assess({"importedFrom": "Smith et al. 2024"}, {})
        assert result.level is Level.DECLARED
        assert "Smith" in result.reason

    @pytest.mark.parametrize("key", ["url", "doi", "citation", "derivedFrom"])
    def test_any_origin_key_counts(self, key: str):
        assert assess({key: "something"}, {}).level is Level.DECLARED

    def test_a_generating_command_is_generated(self):
        assert assess({"generatedBy": "scripts/plot.py"}, {}).level is Level.GENERATED

    def test_generated_says_the_claim_is_unverified(self):
        # The distinction from `reproducible` is the whole point, so the reason
        # must make it obvious rather than sounding like a clean bill of health.
        reason = assess({"generatedBy": "scripts/plot.py"}, {}).reason
        assert "no pipeline stage" in reason

    def test_a_pipeline_stage_outranks_a_sidecar_claim(self):
        stage = calkit.Stage(
            name="plot-cp",
            kind="python-script",
            environment="main",
            entrypoint="scripts/plot_cp.py",
        )
        result = assess({"generatedBy": "something-else.py"}, {}, stage)
        assert result.level is Level.REPRODUCIBLE
        assert "plot-cp" in result.reason

    def test_a_stale_stage_cannot_back_a_reproducible_claim(self):
        """The whole value of `reproducible` is that it can be relied on.

        A stage whose script changed since it last ran did not produce the file
        sitting on disk — an earlier version of it did. Rating that
        `reproducible` would be worst-case wrong: confidently right-looking at
        exactly the moment someone should not trust it.
        """
        stage = calkit.Stage(
            name="plot-cp",
            kind="python-script",
            environment="main",
            entrypoint="scripts/plot_cp.py",
            current=False,
            changed_deps=("scripts/plot_cp.py",),
        )
        result = assess({}, {}, stage)
        assert result.level is Level.GENERATED
        assert "out of date" in result.reason
        assert "scripts/plot_cp.py" in result.reason

    def test_a_stage_that_never_ran_is_not_reproducible(self):
        stage = calkit.Stage(
            name="plot-cp",
            kind="python-script",
            environment="main",
            entrypoint="scripts/plot_cp.py",
            current=None,
        )
        # `None` means "no lock to check against", which is not evidence of
        # staleness — it is the ordinary state of a project that has a pipeline
        # written down but has not run it here yet.
        assert assess({}, {}, stage).level is Level.REPRODUCIBLE

    def test_verified_credentials_are_signed(self):
        result = assess({}, {"validationState": "Valid", "softwareAgent": "Stencila"})
        assert result.level is Level.SIGNED
        assert "Stencila" in result.reason

    def test_trusted_credentials_are_signed(self):
        assert assess({}, {"validationState": "Trusted"}).level is Level.SIGNED

    def test_broken_credentials_are_worse_than_none(self):
        # An `Invalid` manifest means the file changed after signing. Treating
        # that as "has credentials, must be fine" would be exactly backwards.
        result = assess({}, {"validationState": "Invalid"})
        assert result.level is Level.UNIDENTIFIED
        assert "altered after signing" in result.reason

    def test_broken_credentials_do_not_mask_a_real_origin(self):
        result = assess({"doi": "10.1000/x"}, {"validationState": "Invalid"})
        assert result.level is Level.DECLARED


class TestPolicy:
    def test_default_requires_a_declared_origin(self):
        assert DEFAULT_POLICY.require is Level.DECLARED
        assert DEFAULT_POLICY.enforce

    def test_permits_compares_by_rank(self):
        policy = Policy(require=Level.REPRODUCIBLE)
        assert policy.permits(Level.SIGNED)
        assert policy.permits(Level.REPRODUCIBLE)
        assert not policy.permits(Level.GENERATED)

    def test_missing_config_uses_the_default(self, tmp_path: Path):
        assert load_policy(tmp_path) == DEFAULT_POLICY

    def test_reads_the_configured_level(self, tmp_path: Path):
        (tmp_path / "figmint.toml").write_text(
            '[provenance]\nrequire = "reproducible"\n'
        )
        assert load_policy(tmp_path).require is Level.REPRODUCIBLE

    def test_advisory_mode_is_read(self, tmp_path: Path):
        (tmp_path / "figmint.toml").write_text(
            '[provenance]\nrequire = "signed"\nenforce = false\n'
        )
        policy = load_policy(tmp_path)
        assert policy.require is Level.SIGNED
        assert policy.enforce is False

    def test_a_bad_level_raises_rather_than_silently_defaulting(
        self, tmp_path: Path
    ):
        # Silently ignoring a policy someone wrote down is the worst outcome
        # for a feature whose entire job is enforcement.
        (tmp_path / "figmint.toml").write_text('[provenance]\nrequire = "nonsense"\n')
        with pytest.raises(ValueError):
            load_policy(tmp_path)

    def test_a_non_boolean_enforce_raises(self, tmp_path: Path):
        (tmp_path / "figmint.toml").write_text(
            '[provenance]\nenforce = "yes please"\n'
        )
        with pytest.raises(ValueError):
            load_policy(tmp_path)

    def test_config_without_a_provenance_section_uses_the_default(
        self, tmp_path: Path
    ):
        (tmp_path / "figmint.toml").write_text("[something-else]\nkey = 1\n")
        assert load_policy(tmp_path) == DEFAULT_POLICY


class TestCalkitBoundary:
    """figmint reads Calkit's pipeline; it never defines or runs stages."""

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / "calkit.yaml").write_text(
            """
environments:
  main:
    kind: uv-venv
    path: requirements.txt

pipeline:
  stages:
    plot-cp:
      kind: python-script
      script_path: scripts/plot_cp.py
      environment: main
      outputs:
        - figures/cp_curve.svg
        - path: figures/cp_curve.png
          storage: git
    build-paper:
      kind: latex
      target_path: paper/paper.tex
      environment: main
      outputs:
        - paper/paper.pdf
"""
        )
        (tmp_path / "figures").mkdir()
        (tmp_path / "figures" / "cp_curve.svg").write_text("<svg/>")
        (tmp_path / "figures" / "cp_curve.png").write_text("x")
        (tmp_path / "figures" / "stray.svg").write_text("<svg/>")
        return tmp_path

    def test_finds_the_stage_producing_a_file(self, project: Path):
        stage = calkit.Project(project).stage_for(project / "figures/cp_curve.svg")
        assert stage is not None
        assert stage.name == "plot-cp"
        assert stage.entrypoint == "scripts/plot_cp.py"
        assert stage.environment == "main"

    def test_handles_the_mapping_form_of_outputs(self, project: Path):
        # `{path: ..., storage: git}` must resolve like a bare string.
        stage = calkit.Project(project).stage_for(project / "figures/cp_curve.png")
        assert stage is not None and stage.name == "plot-cp"

    def test_files_no_stage_produces_have_none(self, project: Path):
        assert calkit.Project(project).stage_for(project / "figures/stray.svg") is None

    def test_paths_outside_the_project_have_none(self, project: Path, tmp_path: Path):
        outside = tmp_path.parent / "elsewhere.svg"
        assert calkit.Project(project).stage_for(outside) is None

    def test_project_discovery_walks_upward(self, project: Path):
        found = calkit.project_for(project / "figures")
        assert found is not None
        assert found.root == project

    def test_no_calkit_project_is_not_an_error(self, tmp_path: Path):
        assert calkit.project_for(tmp_path) is None

    def lock(self, project: Path, deps: dict[str, str]) -> None:
        """Write a `dvc.lock` recording these dependency hashes for `plot-cp`."""
        import hashlib

        entries = []
        for path, content in deps.items():
            digest = hashlib.md5(content.encode()).hexdigest()
            entries.append(f"    - path: {path}\n      hash: md5\n      md5: {digest}\n")
        (project / "dvc.lock").write_text(
            "schema: '2.0'\nstages:\n  plot-cp:\n    cmd: python scripts/plot_cp.py\n"
            "    deps:\n" + "".join(entries)
        )

    def test_no_lock_file_leaves_freshness_unknown(self, project: Path):
        stage = calkit.Project(project).stage_for(project / "figures/cp_curve.svg")
        assert stage.current is None
        assert stage.verified, "unknown must not be treated as stale"

    def test_matching_hashes_make_the_stage_current(self, project: Path):
        (project / "scripts").mkdir()
        (project / "scripts" / "plot_cp.py").write_text("print(1)")
        self.lock(project, {"scripts/plot_cp.py": "print(1)"})

        stage = calkit.Project(project).stage_for(project / "figures/cp_curve.svg")
        assert stage.current is True
        assert stage.changed_deps == ()

    def test_a_changed_dependency_makes_it_stale(self, project: Path):
        (project / "scripts").mkdir()
        (project / "scripts" / "plot_cp.py").write_text("print(2)")
        self.lock(project, {"scripts/plot_cp.py": "print(1)"})

        stage = calkit.Project(project).stage_for(project / "figures/cp_curve.svg")
        assert stage.current is False
        assert stage.changed_deps == ("scripts/plot_cp.py",)
        assert not stage.verified

    def test_deps_are_reported_for_watching(self, project: Path):
        """`figmint watch` needs these to notice an edited script."""
        (project / "scripts").mkdir()
        (project / "scripts" / "plot_cp.py").write_text("print(1)")
        self.lock(
            project, {"scripts/plot_cp.py": "print(1)", "data/in.csv": "a,b"}
        )
        stage = calkit.Project(project).stage_for(project / "figures/cp_curve.svg")
        assert set(stage.deps) == {"scripts/plot_cp.py", "data/in.csv"}

    def test_a_missing_dependency_counts_as_changed(self, project: Path):
        self.lock(project, {"scripts/gone.py": "print(1)"})
        stage = calkit.Project(project).stage_for(project / "figures/cp_curve.svg")
        assert stage.current is False

    def test_a_stage_absent_from_the_lock_is_unknown(self, project: Path):
        (project / "dvc.lock").write_text(
            "schema: '2.0'\nstages:\n  other:\n    cmd: true\n    deps: []\n"
        )
        stage = calkit.Project(project).stage_for(project / "figures/cp_curve.svg")
        assert stage.current is None

    def test_a_malformed_lock_does_not_crash(self, project: Path):
        (project / "dvc.lock").write_text("stages: [not a mapping")
        stage = calkit.Project(project).stage_for(project / "figures/cp_curve.svg")
        assert stage is not None and stage.current is None

    def test_a_malformed_calkit_yaml_does_not_crash_the_scan(self, tmp_path: Path):
        (tmp_path / "calkit.yaml").write_text("pipeline: [this is not a mapping")
        (tmp_path / "a.svg").write_text("<svg/>")
        project = calkit.Project(tmp_path)
        assert project.stage_for(tmp_path / "a.svg") is None

    def test_an_edited_config_is_picked_up(self, project: Path):
        p = calkit.Project(project)
        assert p.stage_for(project / "figures/stray.svg") is None

        import time

        time.sleep(0.01)
        (project / "calkit.yaml").write_text(
            """
pipeline:
  stages:
    new-stage:
      kind: command
      command: "make stray"
      environment: main
      outputs:
        - figures/stray.svg
"""
        )
        stage = p.stage_for(project / "figures/stray.svg")
        assert stage is not None and stage.name == "new-stage"


class TestImport:
    def test_writes_a_sidecar(self, tmp_path: Path):
        target = tmp_path / "photo.png"
        target.write_text("x")
        sidecar = declare(target, Origin(imported_from="a microscope"))
        assert sidecar.exists()
        assert "importedFrom" in sidecar.read_text()

    def test_lifts_a_file_out_of_unidentified(self, tmp_path: Path):
        from figmint.assets import sidecar_provenance

        target = tmp_path / "photo.png"
        target.write_text("x")
        declare(target, Origin(doi="10.1000/example"))

        provenance = sidecar_provenance(target, tmp_path)
        assert assess(provenance, {}).level is Level.DECLARED

    def test_an_origin_is_required(self, tmp_path: Path):
        target = tmp_path / "photo.png"
        target.write_text("x")
        with pytest.raises(ImportError_, match="stated origin"):
            declare(target, Origin(license="CC-BY"))

    def test_a_missing_file_is_an_error(self, tmp_path: Path):
        with pytest.raises(ImportError_, match="no such file"):
            declare(tmp_path / "ghost.png", Origin(url="http://example.com"))

    def test_merges_into_an_existing_sidecar(self, tmp_path: Path):
        target = tmp_path / "photo.png"
        target.write_text("x")
        declare(target, Origin(url="http://example.com"))
        declare(target, Origin(doi="10.1000/x"))

        text = declare(target, Origin(citation="Smith 2024")).read_text()
        assert "example.com" in text
        assert "10.1000/x" in text
        assert "Smith 2024" in text

    def test_overwrite_discards_the_previous_claim(self, tmp_path: Path):
        target = tmp_path / "photo.png"
        target.write_text("x")
        declare(target, Origin(url="http://old.example.com"))
        text = declare(
            target, Origin(url="http://new.example.com"), overwrite=True
        ).read_text()
        assert "old.example.com" not in text


class TestCheckCli:
    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / "figures").mkdir()
        (tmp_path / "figures" / "mystery.svg").write_text("<svg/>")
        return tmp_path

    def test_anonymous_component_fails_the_default_policy(
        self, project: Path, capsys
    ):
        assert main(["check", "--root", str(project)]) == EXIT_STALE
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "unidentified" in out

    def test_the_failure_names_a_concrete_fix(self, project: Path, capsys):
        main(["check", "--root", str(project)])
        assert "figmint import" in capsys.readouterr().out

    def test_declaring_the_origin_makes_it_pass(self, project: Path, capsys):
        declare(project / "figures" / "mystery.svg", Origin(doi="10.1000/x"))
        assert main(["check", "--root", str(project)]) == EXIT_OK
        assert "satisfy the policy" in capsys.readouterr().out

    def test_advisory_mode_reports_but_does_not_fail(self, project: Path, capsys):
        (project / "figmint.toml").write_text("[provenance]\nenforce = false\n")
        assert main(["check", "--root", str(project)]) == EXIT_OK
        assert "FAIL" in capsys.readouterr().out

    def test_a_stricter_policy_rejects_a_mere_declaration(
        self, project: Path, capsys
    ):
        declare(project / "figures" / "mystery.svg", Origin(doi="10.1000/x"))
        (project / "figmint.toml").write_text(
            '[provenance]\nrequire = "reproducible"\n'
        )
        assert main(["check", "--root", str(project)]) == EXIT_STALE
        out = capsys.readouterr().out
        assert "calkit xr" in out

    def test_a_pipeline_output_satisfies_the_strict_policy(
        self, project: Path, capsys
    ):
        (project / "figmint.toml").write_text(
            '[provenance]\nrequire = "reproducible"\n'
        )
        (project / "calkit.yaml").write_text(
            """
pipeline:
  stages:
    make-it:
      kind: command
      command: "make figures/mystery.svg"
      environment: main
      outputs:
        - figures/mystery.svg
"""
        )
        assert main(["check", "--root", str(project)]) == EXIT_OK

    def test_a_broken_policy_file_is_an_error(self, project: Path, capsys):
        (project / "figmint.toml").write_text('[provenance]\nrequire = "bogus"\n')
        assert main(["check", "--root", str(project)]) == EXIT_ERROR
