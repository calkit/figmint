"""`fromwhere rebuild` — put the project back in order, from the record.

`fromwhere status` can already work out what is wrong and what would fix it: every
artifact carries the command that made it and the inputs it was made from, so
the repair sequence is a property of the record rather than something a person
has to reconstruct. Printing that sequence and asking someone to retype it was
always a half-measure — worse than a half-measure, because a copied command is
a command that can be mistyped, and one wrong `-i` produces a record that is
confidently false.

So this runs it. The order is the derivation order, dependencies first: a
composite rebuilt before the figure it embeds is rebuilt from bytes that are
about to change, which is how you end up running everything twice.

Nothing is rebuilt that does not need it. An artifact whose inputs still hash to
what the record says is left exactly as it is — including its signature, which a
gratuitous re-run would replace with a new one for no reason.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from .status import ArtifactStatus, ancestors, check_all, ordered_repairs
from .store import Artifact, Store


class RebuildError(RuntimeError):
    """Raised when an artifact cannot be rebuilt from what is recorded."""


@dataclass
class RebuildResult:
    #: Artifact paths rebuilt, in the order they were done.
    rebuilt: list[str] = field(default_factory=list)
    #: Paths that need work but cannot be rebuilt, with the reason. A declared
    #: file is the ordinary case: nobody can regenerate raw data.
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.rebuilt and not self.skipped


def _targets(
    store: Store,
    reports: list[ArtifactStatus],
    paths: list[str],
    root: Path,
) -> list[ArtifactStatus]:
    """Which reports are in scope, given the paths asked for.

    Naming a path means "make this one trustworthy", which necessarily includes
    everything behind it — rebuilding a composite while the figure it embeds is
    stale would produce a fresh artifact that is wrong.
    """
    if not paths:
        return list(reports)

    wanted: set[str] = set()
    for path in paths:
        # Resolved against the project, not the process working directory: a
        # path typed on the command line means what it says relative to where
        # the project is, and `store.relative` of a bare name would otherwise
        # depend on where the shell happened to be.
        given = Path(path)
        key = store.relative(given if given.is_absolute() else root / given)
        if key not in store.artifacts:
            raise RebuildError(
                f"nothing recorded for {key}; produce it with `fromwhere run` "
                f"first, so there is a command to repeat"
            )
        wanted.add(key)
        wanted |= ancestors(store, key)
    return [r for r in reports if r.path in wanted]


def plan(
    store: Store,
    reports: list[ArtifactStatus],
    paths: list[str] | None = None,
    root: Path | None = None,
) -> list[ArtifactStatus]:
    """Everything needing work, dependencies first."""
    return ordered_repairs(
        store,
        _targets(store, reports, list(paths or []), root or store.root),
    )


def _rebuild_one(
    artifact: Artifact, store: Store, *, cert: Path | None, key: Path | None
) -> None:
    """Repeat the command that produced one artifact.

    Dispatched in-process rather than by shelling out to `fromwhere`: the
    record holds a command, not a shell line, and re-parsing it through a shell
    would reintroduce every quoting question the record exists to avoid.
    """
    command = artifact.command or ""
    parts = shlex.split(command)

    if parts[:1] == ["fromwhere"]:
        if parts[1:3] == ["drawio", "export"] and len(parts) >= 5:
            from .drawio import export

            export(
                store.root / parts[3],
                store.root / parts[4],
                cert=cert,
                key=key,
            )
            return
        if parts[1:3] == ["gimp", "export"] and len(parts) >= 5:
            from .gimp import export as gimp_export

            gimp_export(store.root / parts[3], store.root / parts[4])
            return
        raise RebuildError(f"cannot repeat `{command}` automatically")

    from .run import run

    run(
        parts,
        inputs=[
            store.root / item.path
            for item in artifact.inputs
            if item.kind == "file"
        ],
        outputs=[store.root / artifact.path],
        cwd=store.root,
        cert=cert,
        key=key,
    )


def rebuild(
    paths: list[str] | None = None,
    *,
    cwd: Path | None = None,
    cert: Path | None = None,
    key: Path | None = None,
    dry_run: bool = False,
) -> RebuildResult:
    """Rebuild whatever is out of date, in derivation order."""
    root = Path(cwd or Path.cwd())
    store = Store.for_path(root)
    result = RebuildResult()

    for report in plan(store, check_all(store.root), paths, store.root):
        artifact = store.artifacts.get(report.path)
        if artifact is None:
            continue
        if not artifact.command:
            # Nobody can regenerate raw data, and a hand-arranged diagram is
            # refreshed by the export that consumes it. Neither is a failure;
            # both are worth saying out loud rather than passing over.
            reason = (
                "declared — nothing produced it"
                if artifact.kind != "authored"
                else "hand-authored — its export refreshes it"
            )
            result.skipped.append((report.path, reason))
            continue

        if dry_run:
            result.rebuilt.append(report.path)
            continue

        # Reloaded each time: the previous step rewrote the record, and this
        # one's inputs are very likely what it just produced.
        store = Store.for_path(root)
        current = store.artifacts.get(report.path)
        _rebuild_one(current or artifact, store, cert=cert, key=key)
        result.rebuilt.append(report.path)

    return result
