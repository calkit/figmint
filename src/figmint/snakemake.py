"""Reading a Snakemake workflow to answer the one question figmint asks.

figmint's `reproducible` rating means *something the project declares can be
checked, without running it*. Calkit stages satisfy that. So do Snakemake rules:
a rule names its outputs, and "which rule produces this file?" has an answer that
is written down rather than asserted.

Withholding the rating from a Snakemake project would have said something false —
that a rigorously reproducible analysis was less accountable than one with a
`.prov.yaml` sidecar claiming a script produced a file. The Palmer Penguins
example made that vivid: every figure came out `unidentified` while an
AI-generated schematic was the best-identified artifact in the repository,
purely because figmint could read one pipeline format and not the other.

What this deliberately does *not* do is execute Snakemake, resolve wildcards
against the filesystem, or evaluate the Python in a Snakefile. It reads the
declarations. A rule whose output is built by an expression it cannot see —
a variable, a function call returning a computed name — is not matched at all,
and the artifact falls back to whatever other evidence it carries. Being unable
to prove a claim is not the same as disproving it.

The one place this can over-claim: an output written `expand("out/{u}.csv",
u=UNIVERSES)` is matched by *pattern*, so `out/baseline.csv` resolves to that
rule whether or not `baseline` is in `UNIVERSES` — and an unrelated `out/notes.csv`
beside it would match too. Refusing to match `expand` at all would drop most
real parameterised workflows, and a file existing on disk under a path the rule
declares is decent corroboration. Evaluating the list would mean interpreting the
Snakefile, which is the line this does not cross.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

from .calkit import Stage

WORKFLOW_NAMES = ("Snakefile", "snakefile", "workflow/Snakefile")

#: `rule name:` / `checkpoint name:` at column zero.
_RULE = re.compile(r"^(?:rule|checkpoint)\s+([A-Za-z_][\w]*)\s*:", re.MULTILINE)
#: A directive inside a rule body — `output:`, `input:`, `params:` …
_DIRECTIVE = re.compile(r"^\s+(\w+)\s*:(.*)$")
#: Quoted string literals, which is what a path looks like in a rule.
_STRING = re.compile(r"""(?:'''|\"\"\"|'|")((?:[^'"\\]|\\.)*)(?:'''|\"\"\"|'|")""")
#: A Snakemake wildcard, `{universe}`.
_WILDCARD = re.compile(r"\{[^{}/]*\}")


def find_project(start: Path) -> Path | None:
    """Walk upward looking for a Snakemake workflow."""
    for directory in [start, *start.parents]:
        if any((directory / name).is_file() for name in WORKFLOW_NAMES):
            return directory
    return None


def _workflow(root: Path) -> Path | None:
    for name in WORKFLOW_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _strings(text: str) -> list[str]:
    """Quoted literals in a directive body, ignoring anything computed.

    `input: data="a.csv", script="b.py"` yields both paths; a keyword name is
    not quoted so it never appears, and `STENCILA + " execute"` contributes a
    fragment that is not a path and is filtered by the caller.
    """
    return [m.group(1) for m in _STRING.finditer(text)]


@dataclass(frozen=True)
class Rule:
    name: str
    outputs: tuple[str, ...]
    inputs: tuple[str, ...]


@functools.lru_cache(maxsize=8)
def _rules(workflow: Path, mtime: float) -> tuple[Rule, ...]:
    """Parse rule declarations out of a Snakefile.

    Deliberately shallow: this is a reader, not an interpreter. Anything it
    cannot recognise it leaves alone rather than guessing.
    """
    try:
        text = workflow.read_text(encoding="utf-8")
    except OSError:
        return ()

    starts = [(m.start(), m.group(1)) for m in _RULE.finditer(text)]
    rules: list[Rule] = []
    for index, (position, name) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        body = text[text.index("\n", position) + 1 : end]

        collected: dict[str, list[str]] = {}
        current: str | None = None
        for line in body.splitlines():
            if not line.strip():
                continue
            match = _DIRECTIVE.match(line)
            if match:
                current = match.group(1)
                collected.setdefault(current, []).extend(_strings(match.group(2)))
            elif current and line.startswith((" ", "\t")):
                # A directive continued onto its own lines.
                collected.setdefault(current, []).extend(_strings(line))

        def paths(key: str) -> tuple[str, ...]:
            found = collected.get(key, [])
            # Shell fragments and format specs are quoted too; a path has no
            # spaces and is not a bare `{...}` reference into another directive.
            return tuple(
                value
                for value in found
                if value
                and " " not in value.strip()
                and not value.strip().startswith("{")
            )

        rules.append(Rule(name=name, outputs=paths("output"), inputs=paths("input")))
    return tuple(rules)


def _matches(declared: str, target: str) -> bool:
    """Whether a declared output covers this path, wildcards included."""
    declared = declared.lstrip("./")
    target = target.lstrip("./")
    if declared == target:
        return True
    if "{" not in declared:
        return False
    # `results/{universe}/x.csv` matches one path segment per wildcard.
    escaped = re.escape(declared)
    pattern = "^" + re.sub(r"\\\{[^{}]*?\\\}", "[^/]+", escaped) + "$"
    return re.match(pattern, target) is not None


@dataclass
class Project:
    """A Snakemake workflow, as far as figmint needs to understand one."""

    root: Path

    @property
    def workflow(self) -> Path | None:
        return _workflow(self.root)

    def _all(self) -> tuple[Rule, ...]:
        workflow = self.workflow
        if workflow is None:
            return ()
        return _rules(workflow, workflow.stat().st_mtime)

    def stage_for(self, path: Path) -> Stage | None:
        """The rule declaring `path` as an output, if any."""
        try:
            relative = Path(path).resolve().relative_to(self.root.resolve()).as_posix()
        except (ValueError, OSError):
            return None

        for rule in self._all():
            if any(_matches(declared, relative) for declared in rule.outputs):
                return Stage(
                    name=rule.name,
                    kind="snakemake-rule",
                    environment=None,
                    entrypoint=next(
                        (i for i in rule.inputs if i.endswith((".py", ".R", ".jl", ".sh"))),
                        None,
                    ),
                    # Snakemake keeps no lock file comparable to `dvc.lock`, so
                    # freshness is unknown rather than false. Claiming a stage is
                    # stale on no evidence would be worse than saying nothing.
                    current=None,
                    deps=rule.inputs,
                    pipeline="Snakemake",
                )
        return None

    def unaccounted_inputs(self, stage: str) -> tuple[str, ...]:
        """Not attempted for Snakemake.

        Calkit declares imported datasets in `calkit.yaml`, which is what makes
        "this input is accounted for" checkable there. Snakemake has no
        equivalent, so reporting every chain root as unexplained would be noise
        rather than a finding.
        """
        return ()

    def stages_consuming(self, path: str) -> tuple[str, ...]:
        target = path.lstrip("./")
        return tuple(
            sorted(
                rule.name
                for rule in self._all()
                if any(i.lstrip("./") == target for i in rule.inputs)
            )
        )


def project_for(path: Path) -> Project | None:
    """The Snakemake workflow containing `path`, if there is one."""
    start = path if path.is_dir() else path.parent
    root = find_project(start)
    return Project(root) if root else None
