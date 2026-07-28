"""Recording that a changed component has been reviewed and accepted.

There are two different questions hiding inside "is this figure stale?":

  1. **Is the built output older than its inputs?** Purely mechanical —
     `figmint build` answers it, and nobody needs to think.
  2. **Has a component changed since it was placed?** Not mechanical at all. The
     axes may have moved, the units may have changed, the conclusion the panel
     supports may no longer hold. Rebuilding does not make that judgement.

So building deliberately does *not* clear a changed-component warning; accepting
does, and accepting is an explicit act. If `build` silently re-recorded hashes,
the staleness warning would be worth nothing — every rebuild would erase the
evidence that anything had changed.

Edits are made with a round-trip YAML parser so comments, key order, and
formatting survive. A format that claims to be human-editable cannot afford to
reformat itself behind the author's back.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ruamel.yaml import YAML

from . import credentials as credentials_mod
from .assets import hash_file, intrinsic_size
from .document import Document
from .status import SourceState, check


@dataclass
class Accepted:
    key: str
    path: str
    old_hash: str | None
    new_hash: str


def _round_trip_yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    # Match the editor's block style:
    #
    #   nodes:
    #     - id: panel-a
    #       type: image
    #
    # In ruamel's terms `sequence` is the column of the item's *content* and
    # `offset` is where the dash sits inside that. sequence must exceed offset,
    # or the dash lands on the content column and the output is invalid YAML.
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 88

    # ruamel writes None as an empty value (`background:`). That parses back to
    # None correctly but reads like an accident in a file people review, so keep
    # the explicit `null` the editor writes.
    yaml.representer.add_representer(
        type(None),
        lambda representer, data: representer.represent_scalar(
            "tag:yaml.org,2002:null", "null"
        ),
    )
    return yaml


def _tidy_number(value: float) -> float | int:
    """Keep whole numbers whole, so `216` doesn't become `216.0` on rewrite."""
    return int(value) if float(value).is_integer() else value


def accept(document: Document, keys: list[str] | None = None) -> list[Accepted]:
    """Re-record the on-disk state of changed components.

    Returns what changed. Only sources reported `stale` are touched — a missing
    file is not something you can accept, and an unchanged one needs nothing.
    """
    report = check(document)
    stale = [
        source
        for source in report.sources
        if source.state is SourceState.STALE
        and (keys is None or source.key in keys)
    ]
    if not stale:
        return []

    yaml = _round_trip_yaml()
    with document.path.open(encoding="utf-8") as handle:
        data = yaml.load(handle)

    accepted: list[Accepted] = []
    now = datetime.now(timezone.utc).isoformat()

    for source in stale:
        path = document.source_path(source.key)
        if path is None or not path.exists():
            continue
        entry = data.get("sources", {}).get(source.key)
        if entry is None:
            continue

        old = entry.get("hash")
        new = hash_file(path)
        entry["hash"] = new
        entry["size"] = path.stat().st_size
        entry["modified"] = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).isoformat()

        # Re-read what the file now says about itself, so the document reflects
        # the accepted version rather than the one it replaced.
        size = intrinsic_size(path)
        if size:
            entry["intrinsic"] = {k: _tidy_number(v) for k, v in size.items()}
        creds = credentials_mod.read(path)
        if creds:
            entry["credentials"] = creds.to_dict()
        elif "credentials" in entry:
            # The component lost its manifest; not recording that would leave a
            # stale claim of provenance in the document.
            del entry["credentials"]

        provenance = entry.get("provenance")
        if provenance is not None:
            provenance["acceptedAt"] = now

        accepted.append(Accepted(source.key, source.path, old, new))

    if not accepted:
        return []

    # Serialize fully before touching the file, then replace atomically, so a
    # failure cannot leave a half-written figure document behind.
    buffer = io.StringIO()
    yaml.dump(data, buffer)
    temporary = document.path.with_suffix(document.path.suffix + ".tmp")
    temporary.write_text(buffer.getvalue(), encoding="utf-8")
    temporary.replace(document.path)

    return accepted
