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

So the only thing figmint asks of Calkit is: **which stage, if any, declares
this file as an output?** Everything else — environments, DVC locks, whether the
stage needs re-running — stays on Calkit's side of the line, reachable through
`calkit status`.
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

    def describe(self) -> str:
        parts = [f"Calkit stage `{self.name}`"]
        if self.entrypoint:
            parts.append(f"({self.entrypoint})")
        return " ".join(parts)


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


@dataclass
class Project:
    """A Calkit project, as far as figmint needs to understand one."""

    root: Path

    @property
    def config(self) -> Path:
        return self.root / CONFIG_NAME

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
        return Stage(
            name=names[0],
            kind=spec.get("kind"),
            environment=spec.get("environment"),
            entrypoint=_entrypoint(spec),
        )

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
