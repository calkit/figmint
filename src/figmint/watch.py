"""Watching the project for component changes.

Step 4 of the demo: an agent edits a plotting script, the script reruns, and the
canvas should show the new panel without anyone pressing a button.

One watcher runs per server and fans out to connected editors, rather than each
browser tab starting its own filesystem watch. On macOS especially, a watch is
not free, and N tabs watching the same tree is N times the work for identical
events.

The watcher deliberately reports only *which paths* changed, not what they now
contain. Clients re-scan through the normal path, so hashes and credentials are
always read by the same code that reads them everywhere else — there is no
second, subtly different freshness path to keep in sync.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from watchfiles import Change, awatch

from .assets import FIGURE_SUFFIXES, SKIP_DIRS
from .document import DOCUMENT_SUFFIX

logger = logging.getLogger(__name__)

#: How long to wait for a burst of writes to settle, in milliseconds. A plotting
#: script writing a multi-megabyte SVG produces many events; reacting to the
#: first one would re-hash a half-written file.
DEBOUNCE_MS = 300

#: Sidecars are provenance, so a change to one is a change to its artifact.
EXTRA_SUFFIXES = {".prov.yaml", ".c2pa"}


def is_relevant(path: Path) -> bool:
    """Whether a changed path is something the editor cares about."""
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    name = path.name
    if name.endswith(DOCUMENT_SUFFIX):
        return True
    if any(name.endswith(suffix) for suffix in EXTRA_SUFFIXES):
        return True
    # Ignore the temporary files our own atomic writes produce, or saving a
    # document would immediately notify every client about a phantom change.
    if name.endswith(".tmp"):
        return False
    return path.suffix.lower() in FIGURE_SUFFIXES


@dataclass
class ChangeSet:
    """Paths that changed in one debounced batch, relative to the project root."""

    assets: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.assets and not self.documents

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "changed",
            "assets": self.assets,
            "documents": self.documents,
        }


def classify(root: Path, raw: set[tuple[Change, str]]) -> ChangeSet:
    """Split a watchfiles batch into asset and document changes."""
    changes = ChangeSet()
    for _, raw_path in raw:
        path = Path(raw_path)
        if not is_relevant(path):
            continue
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if path.name.endswith(DOCUMENT_SUFFIX):
            if relative not in changes.documents:
                changes.documents.append(relative)
        elif relative not in changes.assets:
            changes.assets.append(relative)
    return changes


class Watcher:
    """A single filesystem watch, fanned out to any number of subscribers.

    Started lazily on the first subscriber and stopped when the last one leaves,
    so a server nobody is looking at does no work.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._subscribers: set[asyncio.Queue[ChangeSet]] = set()
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[ChangeSet]]:
        queue: asyncio.Queue[ChangeSet] = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run())
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
            if not self._subscribers:
                await self.stop()

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        logger.info("watching %s", self.root)
        try:
            async for raw in awatch(
                self.root,
                debounce=DEBOUNCE_MS,
                stop_event=self._stop,
                recursive=True,
            ):
                changes = classify(self.root, raw)
                if changes.empty:
                    continue
                self._publish(changes)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a dead watcher must not kill the server
            logger.exception("watcher stopped unexpectedly")

    def _publish(self, changes: ChangeSet) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(changes)
            except asyncio.QueueFull:
                # A client that cannot keep up will re-scan on its next message
                # anyway; dropping is better than blocking the watcher.
                logger.debug("subscriber queue full, dropping change batch")
