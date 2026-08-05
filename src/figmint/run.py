"""`figmint run` — produce an artifact and record where it came from.

The whole design is in the shape of the command:

    figmint run -i data/raw.csv -o figures/plot.png -- uv run plot.py

figmint hashes the inputs, runs the command, hashes what came out, and writes
all of it to `figmint.toml`. It does not inspect the script, parse the command,
or infer anything — it observes. That is why the record can be trusted later:
every hash in it was taken from a file that existed at a known moment, either
side of a command that actually ran.

Three things it insists on, each because the alternative produces a record that
looks complete and is not:

  * **The environment is an input.** See `environments`. A command that cannot
    be tied to a lock file is refused rather than recorded without one.
  * **Inputs are hashed before the command runs.** Hashing afterwards would
    record what the command left behind, which for a script that rewrites its
    own input is a different file.
  * **A command that fails records nothing.** A partially written output with a
    hash beside it is worse than no record at all.
  * **The script is an input, whether or not you said so.** Any file the command
    names is hashed too. Forgetting `-i plot.py` is the easiest mistake to make
    and among the worst to live with: the script is usually the input most
    likely to change, and a record that omits it calls the figure current after
    the code that drew it was rewritten.

Outputs are signed by default, with a local identity created on first use, so
the provenance travels with the file once it leaves the repository. Formats that
cannot carry a manifest are recorded without one rather than failing the run —
a `.drawio` or a `.csv` has provenance worth recording either way.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .environments import Environment, describe, format_command
from .store import Artifact, Input, Store, hash_file


class RunError(RuntimeError):
    """Raised when the artifact cannot be produced or recorded."""


@dataclass
class RunResult:
    artifacts: list[Artifact]
    returncode: int
    environment: Environment
    #: Declared artifacts whose recorded hash was brought up to date because
    #: this run used them. Reported rather than applied silently.
    refreshed: list[str] = field(default_factory=list)


def _files_named_in(command: list[str], cwd: Path, store: Store) -> list[Path]:
    """Files the command itself names — the script, usually.

    Inferred rather than required, because `-i` for the script is the step
    everyone forgets and the omission is invisible: the record looks complete
    and quietly stops noticing code changes.

    Kept narrow on purpose. A token counts only if it resolves to a file that
    exists inside the project, so an option value or a stray word cannot become
    a phantom input. Anything outside the project is left alone — that is the
    interpreter, not the analysis.
    """
    found: list[Path] = []
    root = store.root
    for token in command:
        if token.startswith("-"):
            continue
        candidate = (cwd / token).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file() and candidate not in found:
            found.append(candidate)
    return found


def _collect_inputs(
    paths: list[Path], environment: Environment, store: Store
) -> list[Input]:
    inputs: list[Input] = []
    seen: set[str] = set()

    for path in paths:
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise RunError(f"input does not exist: {path}")
        relative = store.relative(resolved)
        if relative not in seen:
            seen.add(relative)
            inputs.append(Input(relative, hash_file(resolved)))

    if environment.lock is not None:
        inputs.append(
            Input(
                store.relative(environment.lock),
                hash_file(environment.lock),
                kind="environment",
            )
        )
    return inputs


def run(
    command: list[str],
    inputs: list[Path],
    outputs: list[Path],
    *,
    cwd: Path | None = None,
    cert: Path | None = None,
    key: Path | None = None,
    sign: bool = True,
    capture: bool = False,
) -> RunResult:
    """Run a command and record its outputs' provenance."""
    cwd = Path(cwd or Path.cwd()).resolve()
    if not outputs:
        raise RunError("at least one output is required (-o)")

    environment = describe(command, cwd)
    store = Store.for_path(cwd)

    # Before, not after: a script that rewrites its own input would otherwise be
    # recorded against the version it produced rather than the one it read.
    recorded_inputs = _collect_inputs(inputs, environment, store)

    declared = {i.path for i in recorded_inputs}
    for path in _files_named_in(command, cwd, store):
        relative = store.relative(path)
        if relative not in declared:
            declared.add(relative)
            recorded_inputs.append(
                Input(relative, hash_file(path), kind="code")
            )

    for output in outputs:
        Path(output).resolve().parent.mkdir(parents=True, exist_ok=True)

    # Before the command, and written to disk: the command may *read* the
    # record. A document build renders provenance panels out of `figmint.toml`,
    # so refreshing afterwards would bake the pre-run state into the very page
    # this run produces — it would report its own sources as edited, and only a
    # second build would clear it.
    #
    # Refreshing early is honest on its own terms: it records that these bytes
    # are what figmint observed, which is true whether or not the command then
    # succeeds.
    refreshed = _refresh_declared(recorded_inputs, store)
    if refreshed:
        store.save()

    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=capture,
        text=True if capture else None,
    )
    if completed.returncode != 0:
        if capture and completed.stderr:
            sys.stderr.write(completed.stderr)
        raise RunError(
            f"command failed with exit code {completed.returncode}; "
            f"nothing recorded"
        )

    missing = [str(o) for o in outputs if not Path(o).resolve().is_file()]
    if missing:
        raise RunError(
            f"command succeeded but did not produce: {', '.join(missing)}"
        )

    produced: list[Artifact] = []
    for output in outputs:
        resolved = Path(output).resolve()
        # Signed *before* hashing: embedding a manifest changes the bytes, so a
        # hash taken first would describe a file that no longer exists and every
        # signed artifact would read as modified the moment it was written.
        was_signed = False
        if sign:
            was_signed = _sign(
                resolved,
                recorded_inputs,
                store,
                cert,
                key,
                format_command(command),
            )
        artifact = Artifact(
            path=store.relative(resolved),
            hash=hash_file(resolved),
            inputs=list(recorded_inputs),
            command=format_command(command),
            kind="run",
            signed=was_signed,
        )
        store.record(artifact)
        produced.append(artifact)

    store.save()
    return RunResult(
        artifacts=produced,
        returncode=completed.returncode,
        environment=environment,
        refreshed=refreshed,
    )


def _refresh_declared(inputs: list[Input], store: Store) -> list[str]:
    """Bring a declared input's recorded hash up to date with what was used.

    A declaration answers "who is responsible for this file", and that does not
    change when somebody edits a line of it. But `declare` also records a hash,
    and without this every edit to a declared script or document left it sitting
    in `figmint status` as *modified* until it was declared again — a treadmill
    that taught people to re-run `declare` reflexively, which is the last habit
    this tool should be building.

    So a run refreshes it: figmint has just watched the file being used, which
    is first-hand observation rather than a re-assertion of somebody's claim.
    The authorship is untouched.

    Only artifacts with no command are eligible. Anything figmint produced
    itself has a hash that is *evidence*, and quietly rewriting that is exactly
    the tampering the record exists to catch.
    """
    refreshed: list[str] = []
    for item in inputs:
        if item.kind == "environment":
            continue
        artifact = store.artifacts.get(item.path)
        if artifact is None or artifact.command:
            continue
        if artifact.hash == item.hash:
            continue
        artifact.hash = item.hash
        store.record(artifact)
        refreshed.append(item.path)
    return refreshed


def _sign(
    output: Path,
    inputs: list[Input],
    store: Store,
    cert: Path | None,
    key: Path | None,
    command: str,
) -> bool:
    """Embed Content Credentials naming the inputs as ingredients.

    Returns whether a manifest was written. A format c2pa cannot carry one in
    is skipped rather than raising: the artifact is still recorded, and refusing
    to produce a `.drawio` because it cannot be signed would be absurd.
    """
    from . import credentials as credentials_mod
    from . import sign as sign_mod

    if output.suffix.lower() not in credentials_mod.SIGNABLE_SUFFIXES:
        return False

    try:
        sign_mod.sign_artifact(
            output, inputs, store.root, cert=cert, key=key, command=command
        )
    except sign_mod.SigningError as exc:
        raise RunError(f"could not sign {output.name}: {exc}") from exc
    return True
