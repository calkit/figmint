"""Reading a Snakemake workflow.

figmint asks pipelines one question — which stage declares this file as an
output? — and a Snakemake rule answers it as well as a Calkit stage does.
Withholding `reproducible` from a Snakemake project would have measured which
formats figmint can parse while looking like a measurement of rigour.

These tests pin the parser's *limits* as much as its behaviour: it reads
declarations, it does not interpret a Snakefile, and where it cannot tell it
must say so rather than guess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from figmint import pipelines, snakemake
from figmint.provenance import Level, assess

WORKFLOW = '''
import yaml

STENCILA = "../../target/debug/stencila"
UNIVERSES = sorted(glob_wildcards("universes/{universe}.yaml").universe)

rule all:
    input: "figures/plot.png"

rule prepare:
    input: data="raw.csv", script="src/prepare.py"
    output: "clean.csv"
    shell: STENCILA + " execute {input.script}"

rule universe_models:
    input: data="clean.csv", script="src/analyze.py", config="universes/{universe}.yaml"
    output: ".cache/{universe}/models.csv"
    shell: "python {input.script}"

rule plot:
    input:
        data="clean.csv",
        script="src/plot.py"
    output: "figures/plot.png"
    shell: "python {input.script}"

rule computed_output:
    input: "clean.csv"
    output: expand("out/{u}.csv", u=UNIVERSES)
    shell: "true"
'''


@pytest.fixture
def workflow(tmp_path: Path) -> Path:
    (tmp_path / "Snakefile").write_text(WORKFLOW)
    for name in ("raw.csv", "clean.csv"):
        (tmp_path / name).write_text("a,b\n")
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return tmp_path


class TestFindingRules:
    def test_finds_the_rule_that_declares_an_output(self, workflow: Path):
        stage = snakemake.Project(workflow).stage_for(workflow / "figures/plot.png")
        assert stage is not None
        assert stage.name == "plot"
        assert stage.pipeline == "Snakemake"

    def test_reads_a_directive_spread_over_several_lines(self, workflow: Path):
        """`input:` with each key on its own line is the common house style."""
        stage = snakemake.Project(workflow).stage_for(workflow / "figures/plot.png")
        assert set(stage.deps) == {"clean.csv", "src/plot.py"}

    def test_picks_the_script_as_the_entrypoint(self, workflow: Path):
        stage = snakemake.Project(workflow).stage_for(workflow / "clean.csv")
        assert stage.name == "prepare"
        assert stage.entrypoint == "src/prepare.py"

    def test_wildcards_match_one_segment(self, workflow: Path):
        stage = snakemake.Project(workflow).stage_for(
            workflow / ".cache/baseline/models.csv"
        )
        assert stage is not None and stage.name == "universe_models"

    def test_a_wildcard_does_not_match_across_directories(self, workflow: Path):
        assert (
            snakemake.Project(workflow).stage_for(workflow / ".cache/a/b/models.csv")
            is None
        )

    def test_a_file_no_rule_produces_has_none(self, workflow: Path):
        assert snakemake.Project(workflow).stage_for(workflow / "raw.csv") is None

    def test_an_expand_template_matches_by_pattern(self, workflow: Path):
        """`expand("out/{u}.csv", u=UNIVERSES)` matches, without evaluating the list.

        A deliberate limit, and the one place this reader can over-claim: it
        knows the rule's output *pattern* covers `out/baseline.csv`, but not
        whether `baseline` is in `UNIVERSES`. An unrelated `out/notes.csv` in
        that directory would be matched too.

        Accepted because the alternative — refusing to match any `expand` — would
        drop most real parameterised workflows, and because the file existing on
        disk under a path the rule declares is decent corroboration. Evaluating
        the list would mean interpreting the Snakefile, which is the line this
        reader does not cross.
        """
        stage = snakemake.Project(workflow).stage_for(workflow / "out/baseline.csv")
        assert stage is not None and stage.name == "computed_output"

    def test_shell_fragments_are_not_mistaken_for_paths(self, workflow: Path):
        stage = snakemake.Project(workflow).stage_for(workflow / "clean.csv")
        assert not any(" " in dep for dep in stage.deps)

    def test_paths_outside_the_workflow_have_none(self, workflow: Path, tmp_path: Path):
        outside = tmp_path.parent / "elsewhere.png"
        assert snakemake.Project(workflow).stage_for(outside) is None


class TestFreshness:
    def test_freshness_is_unknown_not_false(self, workflow: Path):
        """Snakemake keeps no `dvc.lock` equivalent.

        Reporting a stage as stale on no evidence would be worse than saying
        nothing — it would flag every artifact in every Snakemake project.
        """
        stage = snakemake.Project(workflow).stage_for(workflow / "figures/plot.png")
        assert stage.current is None
        assert stage.verified

    def test_it_still_earns_reproducible(self, workflow: Path):
        stage = snakemake.Project(workflow).stage_for(workflow / "figures/plot.png")
        result = assess({}, {}, stage)
        assert result.level is Level.REPRODUCIBLE
        assert "Snakemake rule" in result.reason


class TestRouting:
    def test_a_snakemake_project_is_found(self, workflow: Path):
        assert pipelines.find_project(workflow) == workflow
        assert isinstance(pipelines.project_for(workflow), snakemake.Project)

    def test_calkit_wins_when_both_are_present(self, workflow: Path):
        """Calkit carries more — environments, and a lock that makes staleness
        checkable — so where both exist it says more."""
        from figmint import calkit

        (workflow / "calkit.yaml").write_text(
            "pipeline:\n  stages:\n    s:\n      kind: command\n"
            "      command: true\n      outputs:\n        - figures/plot.png\n"
        )
        assert isinstance(pipelines.project_for(workflow), calkit.Project)

    def test_no_pipeline_at_all_is_not_an_error(self, tmp_path: Path):
        assert pipelines.project_for(tmp_path) is None
        assert pipelines.find_project(tmp_path) is None

    def test_an_unreadable_workflow_does_not_crash(self, tmp_path: Path):
        (tmp_path / "Snakefile").write_text("rule broken:\n  output:\n")
        project = snakemake.Project(tmp_path)
        assert project.stage_for(tmp_path / "anything.png") is None


class TestConsumers:
    def test_stages_consuming_is_the_inverse_lookup(self, workflow: Path):
        assert "plot" in snakemake.Project(workflow).stages_consuming("clean.csv")

    def test_unaccounted_inputs_is_not_attempted(self, workflow: Path):
        """Calkit declares imported datasets; Snakemake has no equivalent, so
        reporting every chain root would be noise rather than a finding."""
        assert snakemake.Project(workflow).unaccounted_inputs("plot") == ()
