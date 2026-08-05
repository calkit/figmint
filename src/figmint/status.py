"""`figmint status` — is this artifact still an honest picture of its inputs?

A recorded artifact is stale when any input's bytes no longer hash to what the
record says, or when the artifact itself has changed since it was recorded.
Those are different failures and are reported separately:

  * **An input changed.** The artifact is out of date; regenerate it.
  * **The artifact changed.** Something rewrote the output without going
    through figmint, so the record no longer describes the file it names.

Neither is repaired by editing `figmint.toml`, which is the one thing a reader
in a hurry might try. `status` says what to do instead.

There is a third, reported as a warning rather than a failure: an input that
nothing accounts for. Not produced by any recorded command, not declared as
primary — a file that simply appeared. Every check above it passes, which is
precisely why it needs saying: a figure can be current in every link and still
rest on data nobody can place, or on a script nobody will admit to writing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .store import Artifact, Store, hash_file


class State(str, Enum):
    OK = "ok"
    STALE = "stale"
    MISSING = "missing"
    #: Recorded, but the artifact itself no longer matches the record.
    MODIFIED = "modified"
    #: Nothing recorded for this path at all.
    UNTRACKED = "untracked"


@dataclass
class InputStatus:
    path: str
    kind: str
    state: State
    detail: str = ""
    #: True when nothing in the record accounts for this file: no command
    #: produced it and no declaration explains it.
    unaccounted: bool = False


@dataclass
class ArtifactStatus:
    path: str
    state: State
    inputs: list[InputStatus] = field(default_factory=list)
    detail: str = ""
    artifact: Artifact | None = None

    @property
    def stale(self) -> bool:
        return self.state in (State.STALE, State.MISSING, State.MODIFIED)

    @property
    def changed_inputs(self) -> list[InputStatus]:
        return [i for i in self.inputs if i.state is not State.OK]

    @property
    def unaccounted_inputs(self) -> list[InputStatus]:
        return [i for i in self.inputs if i.unaccounted]


def check_artifact(store: Store, artifact: Artifact) -> ArtifactStatus:
    """Compare one recorded artifact against what is on disk now."""
    status = ArtifactStatus(
        path=artifact.path, state=State.OK, artifact=artifact
    )

    target = store.root / artifact.path
    if not target.is_file():
        status.state = State.MISSING
        status.detail = "the artifact no longer exists"
        return status

    for item in artifact.inputs:
        # A lock file is generated from a spec that is already in the project,
        # by the manager named in the command. Demanding an origin for `uv.lock`
        # would be noise, and noise is how a real finding gets ignored.
        #
        # A script is not exempt, though it once was. Code is an input like any
        # other — a figure produced by a script nobody wrote up is as unplaceable
        # as one resting on data nobody collected, and a script a model wrote is
        # exactly what `--with-ai` exists to surface. Declaring it is one
        # command, and it is the whole claim being made about the analysis.
        unaccounted = (
            item.kind != "environment" and item.path not in store.artifacts
        )
        source = store.root / item.path
        if not source.is_file():
            status.inputs.append(
                InputStatus(
                    item.path,
                    item.kind,
                    State.MISSING,
                    "file not found",
                    unaccounted,
                )
            )
            continue
        if hash_file(source) != item.hash:
            status.inputs.append(
                InputStatus(
                    item.path,
                    item.kind,
                    State.STALE,
                    "content changed",
                    unaccounted,
                )
            )
        else:
            status.inputs.append(
                InputStatus(item.path, item.kind, State.OK, "", unaccounted)
            )

    if any(i.state is not State.OK for i in status.inputs):
        status.state = State.STALE
        status.detail = "inputs changed since this was produced"
        return status

    # Only worth checking once the inputs agree: an artifact regenerated from
    # changed inputs is stale, not modified, and saying both would be noise.
    if hash_file(target) != artifact.hash:
        status.state = State.MODIFIED
        status.detail = (
            "the artifact was changed without going through figmint, so the "
            "record no longer describes it"
        )
    return status


def check_path(path: Path) -> ArtifactStatus:
    """Status for one artifact, by path."""
    store = Store.for_path(path)
    key = store.relative(Path(path))
    artifact = store.artifacts.get(key)
    if artifact is None:
        return ArtifactStatus(
            path=key,
            state=State.UNTRACKED,
            detail="nothing recorded for this path; produce it with `figmint run`",
        )
    return check_artifact(store, artifact)


def check_all(root: Path) -> list[ArtifactStatus]:
    """Status for every recorded artifact in a project."""
    store = Store.load(root)
    return [
        check_artifact(store, a) for _, a in sorted(store.artifacts.items())
    ]
