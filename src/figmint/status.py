"""`figmint status` — is this artifact still an honest picture of its inputs?

A recorded artifact is stale when any input's bytes no longer hash to what the
record says, or when the artifact itself has changed since it was recorded.
Those are different failures and are reported separately:

  * **An input changed.** The artifact is out of date; regenerate it.
  * **The artifact changed.** Something rewrote the output without going
    through figmint, so the record no longer describes the file it names.

Neither is repaired by editing `figmint.toml`, which is the one thing a reader
in a hurry might try. `status` says what to do instead.

A *declared* artifact is not checked against its own hash. Nothing produced it,
so its bytes changing means a person edited it, and what matters about that is
whether the edit reached an output — which the input hashes on each output
already record.

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
    #: A *produced* artifact no longer matches the record: something rewrote it
    #: without going through figmint.
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
    #: Artifacts further back in this one's chain that are not OK. Staleness
    #: does not stop at the first link: a composite whose `.drawio` is untouched
    #: passes every direct check even when the data three steps back was
    #: tampered with, because nothing regenerated the panel in between. Reported
    #: separately from `state`, which stays a statement about this file alone.
    upstream: list[str] = field(default_factory=list)
    #: For a *declared* file: the artifacts that were built from bytes it no
    #: longer has. Its own hash is not checked — a declaration is not a claim
    #: about content — but "are these the bytes the things downstream were made
    #: from?" is a different question, and one worth answering: it names the
    #: file somebody edited rather than only the figure that stopped matching.
    diverged_from: list[str] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return self.state in (State.STALE, State.MISSING, State.MODIFIED)

    @property
    def changed(self) -> bool:
        """Whether this declared file differs from what was built from it."""
        return bool(self.diverged_from)

    @property
    def trustworthy(self) -> bool:
        """Whether this artifact can be believed, chain and all."""
        return not self.stale and not self.upstream

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

    # Only a *produced* artifact is checked against its own hash. Nothing made a
    # declared one, so its bytes changing means a person edited it — which is
    # allowed, and which the declaration does not speak to either way: a
    # declaration says who is answerable for a file, not what it contained on
    # some particular afternoon.
    #
    # Nor is anything lost by not checking. What matters about an edit is
    # whether it reached an output, and that is already recorded — every output
    # carries the hash of each input as it was when the output was made, and
    # those are recomputed whenever `figmint run` regenerates it. An edit that
    # affects something shows up there; an edit that affects nothing is not a
    # finding.
    if artifact.command and hash_file(target) != artifact.hash:
        status.state = State.MODIFIED
        status.detail = (
            "the artifact was changed without going through figmint, so the "
            "record no longer describes it"
        )
    return status


def project_store(path: Path | None = None) -> Store:
    """The store a status command is talking about."""
    return Store.for_path(Path(path) if path else Path.cwd())


def rebuild_command(artifact: Artifact) -> str | None:
    """The command that would bring this artifact up to date.

    Reconstructed as a full `figmint run` rather than handed back as the bare
    command that was recorded: re-running the inner command directly would
    produce the file *outside* figmint, and the record would then report it as
    modified — turning "stale" into "tampered with" and making things worse.
    """
    if not artifact.command:
        return None
    if artifact.command.startswith("figmint "):
        # Already a figmint invocation: `drawio export`, and friends.
        return artifact.command

    parts = ["figmint", "run"]
    for item in artifact.inputs:
        # The lock file is discovered from the command, not passed in, and the
        # script is inferred — repeating either would be noise the user then has
        # to understand before trusting the rest of the line.
        if item.kind == "file":
            parts += ["-i", item.path]
    parts += ["-o", artifact.path, "--", artifact.command]
    return " ".join(parts)


def ordered_repairs(
    store: Store, reports: list[ArtifactStatus]
) -> list[ArtifactStatus]:
    """Everything needing work, in an order that actually works.

    Alphabetical would be actively misleading here: rebuilding the document
    before the figure it embeds accomplishes nothing, and the user would run
    everything twice before noticing. Dependencies first.
    """
    broken = [r for r in reports if not r.trustworthy]
    return sorted(
        broken, key=lambda r: (len(ancestors(store, r.path)), r.path)
    )


def rebuild_plan(store: Store, reports: list[ArtifactStatus]) -> list[str]:
    """The repair sequence as commands a person could run."""
    plan: list[str] = []
    for report in ordered_repairs(store, reports):
        artifact = store.artifacts.get(report.path)
        if artifact is None:
            continue
        for command in _steps_for(artifact, report):
            if command not in plan:
                plan.append(command)
    return plan


def _steps_for(artifact: Artifact, report: ArtifactStatus) -> list[str]:
    """The commands that would repair one artifact.

    A hand-authored diagram gets nothing of its own: exporting it re-embeds any
    panel that has been redrawn, so the export step already in the plan covers
    it, and naming an import here would be busywork the tool has stopped
    needing.
    """
    del report
    command = rebuild_command(artifact)
    return [command] if command else []


def consumers_disagreeing(
    store: Store, path: str, ignore: set[str] | None = None
) -> list[str]:
    """Artifacts recorded as built from bytes this file no longer has.

    `ignore` drops consumers the caller is not reporting on — a document panel
    excludes its own output, and a source that "changed" only relative to that
    output has changed only relative to something the reader is already looking
    at.
    """
    target = store.root / path
    if not target.is_file():
        return []
    current = hash_file(target)
    skip = ignore or set()
    return sorted(
        name
        for name, artifact in store.artifacts.items()
        if name not in skip
        and any(i.path == path and i.hash != current for i in artifact.inputs)
    )


def mark_divergence(
    store: Store, reports: list[ArtifactStatus], ignore: set[str] | None = None
) -> None:
    """Fill in `diverged_from` for every declared artifact in `reports`."""
    for report in reports:
        artifact = store.artifacts.get(report.path)
        if artifact is None or artifact.command:
            continue
        report.diverged_from = consumers_disagreeing(
            store, report.path, ignore
        )


def ancestors(store: Store, path: str) -> set[str]:
    """Everything an artifact came from, transitively, excluding itself."""
    seen: set[str] = set()
    queue = [path]
    while queue:
        artifact = store.artifacts.get(queue.pop())
        if artifact is None:
            continue
        for item in artifact.inputs:
            if item.path not in seen:
                seen.add(item.path)
                queue.append(item.path)
    seen.discard(path)
    return seen


def _upstream_trouble(store: Store, path: str) -> list[str]:
    """Which artifacts behind this one are not OK.

    Without this an artifact can be sound in every direct link and still rest
    on a file that was tampered with, because nothing in between was
    regenerated — and every check would pass.
    """
    behind = ancestors(store, path)
    return sorted(
        p
        for p in behind
        if p in store.artifacts
        and check_artifact(store, store.artifacts[p]).stale
    )


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
    report = check_artifact(store, artifact)
    report.upstream = _upstream_trouble(store, key)
    mark_divergence(store, [report])
    return report


def check_all(root: Path) -> list[ArtifactStatus]:
    """Status for every recorded artifact in a project."""
    store = Store.load(root)
    reports = [
        check_artifact(store, a) for _, a in sorted(store.artifacts.items())
    ]
    troubled = {r.path for r in reports if r.stale}
    if troubled:
        for report in reports:
            report.upstream = sorted(troubled & ancestors(store, report.path))
    mark_divergence(store, reports)
    return reports
