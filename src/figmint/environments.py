"""Which environment a command runs in, and the lock file that pins it.

figmint refuses to record an output produced by a bare command, and the refusal
is the point. "This figure came from `plot.py` and `data.csv`" is a claim about
two files; the same script under a different NumPy produces a different picture,
and nothing in that claim would notice. The environment is an input. Treating it
as one is the difference between a provenance record and a filename.

So a command has to arrive through a manager that writes a *deterministic* lock
file, and that lock is hashed alongside everything else. A manager that only
records loose constraints would put a hash in the record that does not pin what
actually ran, which is worse than no hash: it looks like evidence.

This module only *reads* the command. It does not run anything, does not create
or validate environments, and does not check that the manager is installed —
those are the manager's job, and duplicating them would mean figmint holding
opinions about environments it has no business holding.

Calkit is the exception to "only reads", and deliberately so. It fronts several
backends — Docker, Conda, Julia, renv — each with its own lock in its own place,
and guessing would mean reimplementing Calkit's own resolution. It is asked
instead: `calkit describe env -n <name>` returns the lock path, so the answer
comes from the tool that owns it.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


class EnvironmentError_(RuntimeError):
    """Raised when a command cannot be tied to a lock file."""


@dataclass(frozen=True)
class Environment:
    """How a command is run, and what pins it."""

    manager: str
    #: The lock file, as an absolute path.
    lock: Path | None


@dataclass(frozen=True)
class Manager:
    """A recognized command prefix and the lock it implies."""

    #: Words the command must begin with.
    prefix: tuple[str, ...]
    name: str
    #: Candidate lock filenames, searched upward from the working directory.
    #: The first that exists wins.
    locks: tuple[str, ...]
    #: What to tell someone whose project has none of them yet.
    remedy: str


MANAGERS: tuple[Manager, ...] = (
    Manager(("uv", "run"), "uv", ("uv.lock",), "run `uv lock`"),
    Manager(("pixi", "run"), "pixi", ("pixi.lock",), "run `pixi install`"),
    Manager(
        ("bun", "run"), "bun", ("bun.lock", "bun.lockb"), "run `bun install`"
    ),
    Manager(
        ("cargo", "run"),
        "cargo",
        ("Cargo.lock",),
        "run `cargo generate-lockfile`",
    ),
    Manager(
        ("nix", "develop", "--command"),
        "nix",
        ("flake.lock",),
        "run `nix flake lock`",
    ),
)

#: Calkit subcommands that run something inside a managed environment.
CALKIT_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("xenv",),
    ("nb", "exec"),
    ("latex", "build"),
)


def _find_upward(start: Path, names: tuple[str, ...]) -> Path | None:
    start = Path(start).resolve()
    for directory in [start, *start.parents]:
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def _environment_name(command: list[str]) -> str | None:
    for index, part in enumerate(command):
        if part in ("-n", "--name") and index + 1 < len(command):
            return command[index + 1]
        if part.startswith("--name="):
            return part.split("=", 1)[1]
    return None


def _calkit_lock(command: list[str], cwd: Path) -> Path:
    """Ask Calkit where the lock for this environment lives.

    Calkit fronts Docker, Conda, Julia and renv, each locking somewhere
    different. Rather than reimplement that resolution — and drift from it —
    the tool that owns the answer is asked for it.
    """
    name = _environment_name(command)
    if not name:
        raise EnvironmentError_(
            "a Calkit command needs `-n <environment>` so figmint can ask "
            "Calkit which lock file pins it"
        )

    try:
        completed = subprocess.run(
            ["calkit", "describe", "env", "-n", name],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise EnvironmentError_(
            "`calkit` is not on PATH, so the environment's lock file cannot be "
            "resolved"
        ) from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise EnvironmentError_(
            f"`calkit describe env -n {name}` failed"
            + (f": {detail}" if detail else "")
        )

    try:
        described = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise EnvironmentError_(
            f"could not read the output of `calkit describe env -n {name}`"
        ) from exc

    lock = described.get("lock_path")
    if not lock:
        raise EnvironmentError_(
            f"Calkit environment `{name}` ({described.get('kind', 'unknown')}) "
            f"reports no lock file, so what it pins cannot be recorded"
        )

    resolved: Path = (Path(cwd) / lock).resolve()
    if not resolved.is_file():
        raise EnvironmentError_(
            f"Calkit names `{lock}` as the lock for `{name}`, but it does not "
            f"exist yet; run the environment once so Calkit writes it"
        )
    return resolved


def describe(command: list[str], cwd: Path) -> Environment:
    """Identify the environment manager in `command` and locate its lock file.

    Raises when the command is not run through a recognized manager. A hard
    failure rather than a warning: recording an artifact whose environment is
    unknown would put a claim in `figmint.toml` that the file cannot support,
    and the whole value of the record is that everything in it is checkable.
    """
    if not command:
        raise EnvironmentError_("no command given")

    cwd = Path(cwd).resolve()

    if command[0] in ("calkit", "ck"):
        rest = command[1:]
        for prefix in CALKIT_PREFIXES:
            if tuple(rest[: len(prefix)]) == prefix:
                return Environment(
                    manager="calkit", lock=_calkit_lock(rest, cwd)
                )
        raise EnvironmentError_(
            f"`{shlex.join(command[:3])}` is not a Calkit command that runs "
            f"inside a managed environment. Use `calkit xenv`, "
            f"`calkit nb exec`, or `calkit latex build`."
        )

    for manager in MANAGERS:
        if tuple(command[: len(manager.prefix)]) == manager.prefix:
            lock = _find_upward(cwd, manager.locks)
            if lock is None:
                names = " or ".join(manager.locks)
                raise EnvironmentError_(
                    f"`{' '.join(manager.prefix)}` was used but no {names} was "
                    f"found; {manager.remedy} so the environment can be recorded"
                )
            return Environment(manager=manager.name, lock=lock)

    accepted = ", ".join("`" + " ".join(m.prefix) + "`" for m in MANAGERS)
    raise EnvironmentError_(
        f"`{shlex.join(command[:3])}` is not run through a recognized "
        f"environment manager. The command must start with one of {accepted}, "
        f"`calkit xenv`, `calkit nb exec`, or `calkit latex build`, so the "
        f"environment can be hashed as an input alongside the data."
    )


def format_command(command: list[str]) -> str:
    """The command as a single string, quoted so it can be re-run verbatim."""
    return shlex.join(command)
