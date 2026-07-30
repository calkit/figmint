"""Reading a Calkit project's pipeline to verify how a component was produced.

Module boundary
---------------
figmint **references** stages; Calkit **defines and runs** them.

The tempting alternative — embedding a stage definition (kind, script,
environment) inside the figmint document — was rejected. It would mean
duplicating Calkit's schema in a second file that can drift from the first, and
figmint would then need to resolve environments, manage locks, and execute
things to make the definition worth anything. Calkit already does all of that,
well. Two sources of truth for one stage is the bug, not the feature.

What figmint gets from referencing instead is stronger anyway: a *verifiable*
claim. "This component was produced by script X" is a self-assertion when it
sits in a sidecar. "Stage `plot-cp` in calkit.yaml declares this exact path as
an output" is checkable, and it is checkable without running anything.

So the first thing figmint asks of Calkit is: **which stage, if any, declares
this file as an output?** Environments, locks, and actually running anything stay
on Calkit's side of the line.

The second thing is a consequence of the first. "Stage `plot-cp` declares this
path as an output" only earns the `reproducible` rating while the stage is
*current*. Edit `scripts/plot_cp.py` and don't re-run, and the file on disk is
the output of a stage that no longer exists as written — the claim is about a
previous version of the code. Rating that `reproducible` would be worse than
useless, because it is exactly the situation where someone would rely on the
rating and be wrong.

Answering it means reading `dvc.lock`, which records the hash of every dependency
as of the last successful run: compare those against the files now and a changed
dependency is a stale stage. That is a read of Calkit's output, not a
reimplementation of Calkit — figmint still never decides what a stage is, when to
run it, or how. It only declines to vouch for a stage that Calkit would rerun.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_NAME = "calkit.yaml"


@dataclass(frozen=True)
class Stage:
    """A pipeline stage that produces a file."""

    name: str
    kind: str | None
    environment: str | None
    #: Whatever identifies the work: script path, command, notebook.
    entrypoint: str | None
    #: Whether `dvc.lock` still matches this stage's dependencies *and* those of
    #: every stage upstream of it. `None` means unknown — no lock file, so the
    #: stage has never run here.
    current: bool | None = None
    #: Dependencies that no longer match what the lock recorded.
    changed_deps: tuple[str, ...] = ()
    #: The stage those dependencies belong to, which is not always this one:
    #: editing a diagram makes `embed-figure` stale while `render-figure`, whose
    #: only input is a file `embed-figure` has yet to rewrite, still looks fine.
    stale_stage: str | None = None
    #: Every declared dependency, whether or not it changed. Used by `watch`, so
    #: that editing a plotting script updates a preview even though the script
    #: is not itself a component.
    deps: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        """Whether this stage can currently back a `reproducible` claim."""
        return self.current is not False

    def describe(self) -> str:
        parts = [f"Calkit stage `{self.name}`"]
        if self.entrypoint:
            parts.append(f"({self.entrypoint})")
        return " ".join(parts)

    def describe_staleness(self) -> str:
        """Why the stage cannot be vouched for, in a form worth printing."""
        if self.current is None:
            return (
                f"stage `{self.name}` declares it, but `dvc.lock` has no record "
                f"of it ever running here"
            )
        changed = ", ".join(self.changed_deps) or "its dependencies"
        blamed = self.stale_stage or self.name
        via = "" if blamed == self.name else f" (upstream of `{self.name}`)"
        return (
            f"stage `{blamed}`{via} is out of date: {changed} changed since it "
            f"last ran, so this file is the output of a previous version"
        )


def _output_paths(outputs: Any) -> list[str]:
    """Normalize a stage's `outputs`, which may be strings or mappings."""
    paths: list[str] = []
    for entry in outputs or []:
        if isinstance(entry, str):
            paths.append(entry)
        elif isinstance(entry, dict):
            # `{path: ..., storage: git}` form.
            path = entry.get("path")
            if isinstance(path, str):
                paths.append(path)
    return paths


#: Artifact sections of `calkit.yaml` that can declare where a file came from.
ARTIFACT_SECTIONS = ("datasets", "figures", "publications")


def _artifact_entries(section: Any) -> list[dict[str, Any]]:
    """Normalize an artifact section, which may be a list or a mapping.

    Calkit's own models use a list; a mapping keyed by name is the friendlier
    form to write by hand and appears in real projects. Accept both rather than
    silently finding nothing, because finding nothing here reads as "no problem".
    """
    if isinstance(section, list):
        return [entry for entry in section if isinstance(entry, dict)]
    if isinstance(section, dict):
        return [entry for entry in section.values() if isinstance(entry, dict)]
    return []


def _stage_input_paths(inputs: Any) -> tuple[list[str], list[str]]:
    """Split a stage's `inputs` into plain paths and `from_stage_outputs` names."""
    paths: list[str] = []
    stages: list[str] = []
    for entry in inputs or []:
        if isinstance(entry, str):
            paths.append(entry)
        elif isinstance(entry, dict):
            upstream = entry.get("from_stage_outputs")
            if isinstance(upstream, str):
                stages.append(upstream)
                continue
            path = entry.get("path")
            if isinstance(path, str):
                paths.append(path)
    return paths, stages


#: Inputs that are the project's own source rather than data it consumes.
#: Asking where these "came from" is not a meaningful question — they are in the
#: repository, under version control, alongside everything else.
_SOURCE_SUFFIXES = (".py", ".r", ".jl", ".m", ".sh", ".tex", ".ipynb", ".lock")
_SOURCE_NAMES = ("uv.lock", "requirements.txt", "pyproject.toml", "environment.yml")


def _is_project_source(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in _SOURCE_NAMES or path.lower().endswith(_SOURCE_SUFFIXES)


def _entrypoint(spec: dict[str, Any]) -> str | None:
    for key in ("script_path", "notebook_path", "target_path", "command"):
        value = spec.get(key)
        if isinstance(value, str):
            return value
    return None


def find_project(start: Path) -> Path | None:
    """Walk upward looking for a Calkit project root."""
    for directory in [start, *start.parents]:
        if (directory / CONFIG_NAME).is_file():
            return directory
    return None


@functools.lru_cache(maxsize=8)
def _load(config: Path, mtime: float) -> dict[str, list[str]]:
    """Map output path -> producing stage name.

    Keyed on mtime so an edited `calkit.yaml` is picked up without restarting,
    while a directory scan of a hundred files parses it once.
    """
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}

    pipeline = data.get("pipeline")
    stages = pipeline.get("stages") if isinstance(pipeline, dict) else None
    if not isinstance(stages, dict):
        return {}

    index: dict[str, list[str]] = {}
    for name, spec in stages.items():
        if not isinstance(spec, dict):
            continue
        wdir = spec.get("wdir")
        for path in _output_paths(spec.get("outputs")):
            # Stage paths are relative to the repo root, or to `wdir` when set.
            resolved = f"{wdir.rstrip('/')}/{path}" if isinstance(wdir, str) else path
            index.setdefault(resolved.lstrip("./"), []).append(str(name))
    return index


@functools.lru_cache(maxsize=8)
def _load_artifacts(config: Path, mtime: float) -> dict[str, dict[str, Any]]:
    """Map declared artifact path -> its entry, across every artifact section."""
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for name in ARTIFACT_SECTIONS:
        for entry in _artifact_entries(data.get(name)):
            path = entry.get("path")
            if isinstance(path, str):
                out.setdefault(path.lstrip("./"), entry)
    return out


def _artifact_is_accounted(entry: dict[str, Any]) -> bool:
    """Whether a `calkit.yaml` entry says where its file came from.

    The same test Calkit's own `calkit check` applies: an artifact is accounted
    for if a stage produces it or `imported_from` says where it was taken from.
    A `title` and a `description` are documentation, not provenance — they say
    what a file is, never where it came from or how to get it again.
    """
    return entry.get("stage") is not None or entry.get("imported_from") is not None


LOCK_NAME = "dvc.lock"


def _md5(path: Path) -> str | None:
    """DVC's dependency hash. Plain MD5 of the bytes, for a file."""
    import hashlib

    try:
        return hashlib.md5(path.read_bytes()).hexdigest()
    except OSError:
        return None


@functools.lru_cache(maxsize=8)
def _load_lock(lock: Path, mtime: float) -> dict[str, list[dict[str, Any]]]:
    """Map stage name -> recorded dependencies, from `dvc.lock`."""
    try:
        data = yaml.safe_load(lock.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    stages = data.get("stages")
    if not isinstance(stages, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for name, spec in stages.items():
        if isinstance(spec, dict) and isinstance(spec.get("deps"), list):
            out[str(name)] = [d for d in spec["deps"] if isinstance(d, dict)]
    return out


@dataclass
class Project:
    """A Calkit project, as far as figmint needs to understand one."""

    root: Path

    @property
    def config(self) -> Path:
        return self.root / CONFIG_NAME

    @property
    def lock(self) -> Path:
        return self.root / LOCK_NAME

    def _stages(self) -> dict[str, Any]:
        config = self.config
        if not config.is_file():
            return {}
        try:
            data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        stages = (data.get("pipeline") or {}).get("stages") or {}
        return stages if isinstance(stages, dict) else {}

    def _ancestry(self, name: str) -> list[str]:
        """This stage and every stage it transitively depends on."""
        stages = self._stages()
        order: list[str] = []
        queue = [name]
        while queue:
            current = queue.pop()
            if current in order:
                continue
            order.append(current)
            spec = stages.get(current)
            if isinstance(spec, dict):
                _, upstream = _stage_input_paths(spec.get("inputs"))
                queue.extend(upstream)
        return order

    def _freshness_transitive(
        self, name: str, spec: dict[str, Any] | None = None
    ) -> tuple[bool | None, tuple[str, ...], tuple[str, ...], str | None]:
        """Freshness of a stage *and its whole ancestry*.

        Checking a stage alone is not enough, and the failure is quiet. Edit
        `figures/composite.drawio` and `embed-figure` goes stale — but
        `render-figure`, which produces the published picture, depends only on a
        file `embed-figure` has yet to rewrite, so in isolation it still looks
        current. The document would then be reported as up to date while showing
        a rendering of a diagram that no longer exists.

        A stage can only be vouched for if everything behind it can be too.
        """
        stages = self._stages()
        deps: list[str] = []
        for stage_name in self._ancestry(name):
            current, changed, stage_deps = self._freshness(
                stage_name, stages.get(stage_name)
            )
            deps.extend(stage_deps)
            if current is False:
                return False, changed, tuple(dict.fromkeys(deps)), stage_name
        # Unknown only matters for the stage itself; an ancestor with no lock
        # entry says nothing about whether this output is current.
        own_current, _, _ = self._freshness(name, spec or stages.get(name))
        return own_current, (), tuple(dict.fromkeys(deps)), None

    def _freshness(
        self, name: str, spec: dict[str, Any] | None = None
    ) -> tuple[bool | None, tuple[str, ...], tuple[str, ...]]:
        """Whether a stage's recorded dependencies still match the disk.

        Returns `(current, changed, all_deps)`. `current` is None when the stage
        has no lock entry, which means it has never run in this working copy —
        distinct from "ran, and something changed since".

        Read straight from the lock, with no cleverness about whether the lock
        might be mid-update. An earlier version guarded against that by treating
        a lock older than the stage's outputs as unusable, on the theory that a
        stage running inside a pipeline would read the previous run's lock. DVC
        turns out to write `dvc.lock` incrementally, after each stage, so the
        guard never fired for the case it was written for — and it did fire when
        someone regenerated an output by hand after editing the script, which is
        precisely when the staleness warning is worth having.
        """
        lock = self.lock
        if not lock.is_file():
            return None, (), ()

        lock_mtime = lock.stat().st_mtime
        recorded = _load_lock(lock, lock_mtime).get(name)
        if recorded is None:
            return None, (), ()

        deps: list[str] = []
        changed: list[str] = []
        for entry in recorded:
            path = entry.get("path")
            if not isinstance(path, str):
                continue
            deps.append(path)
            expected = entry.get("md5")
            if not isinstance(expected, str):
                # Directory dependencies are recorded as a `files` list rather
                # than one hash. Not worth walking; treat as unverifiable rather
                # than pretending it changed.
                continue
            if _md5(self.root / path) != expected:
                changed.append(path)
        return (not changed), tuple(changed), tuple(deps)

    def stage_for(self, path: Path) -> Stage | None:
        """The pipeline stage declaring `path` as an output, if any."""
        config = self.config
        if not config.is_file():
            return None
        try:
            relative = path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return None

        index = _load(config, config.stat().st_mtime)
        names = index.get(relative)
        if not names:
            return None

        spec = self._stage_spec(names[0])
        current, changed, deps, blamed = self._freshness_transitive(names[0], spec)
        return Stage(
            name=names[0],
            kind=spec.get("kind"),
            environment=spec.get("environment"),
            entrypoint=_entrypoint(spec),
            current=current,
            changed_deps=changed,
            stale_stage=blamed,
            deps=deps,
        )

    def unaccounted_inputs(self, stage: str) -> tuple[str, ...]:
        """Inputs the pipeline consumes but nothing in the project accounts for.

        A figure can be perfectly `reproducible` — a stage declares it, the stage
        is current, the lock agrees — and still rest on a CSV that simply
        appeared in the repository one day. The chain of custody is only as good
        as the thing at the bottom of it, and that is exactly the link nobody
        notices, because every automated check upstream of it passes.

        This walks the stage's inputs transitively through `from_stage_outputs`,
        skips anything another stage produces, and reports what is left: the
        roots. A root is accounted for when `calkit.yaml` declares it with an
        `imported_from` (or a producing stage). Anything else is a file with no
        stated origin.

        Scripts and environment locks are excluded. They are inputs, but they are
        the project's own source, versioned in Git alongside everything else —
        asking where `scripts/plot_cp.py` came from is not the question this is
        for.
        """
        config = self.config
        if not config.is_file():
            return ()
        try:
            data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return ()
        stages = (data.get("pipeline") or {}).get("stages") or {}
        if not isinstance(stages, dict):
            return ()

        produced = set(_load(config, config.stat().st_mtime))
        artifacts = _load_artifacts(config, config.stat().st_mtime)

        roots: list[str] = []
        seen_stages: set[str] = set()
        queue = [stage]
        while queue:
            name = queue.pop()
            if name in seen_stages:
                continue
            seen_stages.add(name)
            spec = stages.get(name)
            if not isinstance(spec, dict):
                continue
            paths, upstream = _stage_input_paths(spec.get("inputs"))
            queue.extend(upstream)
            entrypoint = _entrypoint(spec)
            for path in paths:
                clean = path.lstrip("./")
                if clean in produced or clean == entrypoint:
                    continue
                if _is_project_source(clean):
                    continue
                entry = artifacts.get(clean)
                if entry is not None and _artifact_is_accounted(entry):
                    continue
                roots.append(clean)
        return tuple(sorted(set(roots)))

    def _stage_spec(self, name: str) -> dict[str, Any]:
        try:
            data = yaml.safe_load(self.config.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        stages = (data.get("pipeline") or {}).get("stages") or {}
        spec = stages.get(name)
        return spec if isinstance(spec, dict) else {}


def project_for(path: Path) -> Project | None:
    """The Calkit project containing `path`, if there is one."""
    root = find_project(path if path.is_dir() else path.parent)
    return Project(root) if root else None
