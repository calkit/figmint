"""`figmint declare` — put a primary artifact's origin on the record.

`figmint run` records what figmint watched being made. This records what it did
not: the measurements, the downloaded dataset, the image somebody was handed.
Without it those files sit at the bottom of the chain unexplained, and every
check above them passes — which is what makes the gap easy to miss.
"""

from __future__ import annotations

from pathlib import Path

from .origins import (
    Origin,
    OriginError,
    attested,
    git_author,
    parse_doi,
    parse_location,
)
from .store import Artifact, Store, hash_file


def resolve_origin(
    *,
    mine: bool = False,
    author: str | None = None,
    with_ai: str | None = None,
    doi: str | None = None,
    git: str | None = None,
    calkit: str | None = None,
    cwd: Path | None = None,
) -> Origin:
    """Turn the command-line options into one claim.

    Exactly one, deliberately. A file that is both "mine" and fetched from a DOI
    is two different stories, and a record that holds both says neither.
    """
    claims = {
        "--mine": bool(mine or author),
        "--doi": bool(doi),
        "--git": bool(git),
        "--calkit": bool(calkit),
    }
    given = [flag for flag, present in claims.items() if present]

    # `--with-ai` modifies an attestation rather than replacing one, so its own
    # errors come first: they say something specific about what is wrong, and
    # "say where it came from" would be technically true but useless to someone
    # who just told us exactly that.
    if with_ai:
        if doi or git or calkit:
            raise OriginError(
                "`--with-ai` belongs on an attestation. A published or "
                "revision-pinned source already says where the file came from."
            )
        if not claims["--mine"]:
            raise OriginError(
                "`--with-ai` names a tool, and a tool cannot be accountable "
                "for a file. Say who is: add `--mine`, or `--author 'Their "
                "Name'` if you are declaring on someone's behalf."
            )

    if not given:
        raise OriginError(
            "say where it came from: `--mine` if you produced it, `--doi` if it "
            "is published, or `--git`/`--calkit` with a `location@rev` if it "
            "lives in another project."
        )
    if len(given) > 1:
        raise OriginError(
            f"give exactly one origin, not {' and '.join(given)}"
        )

    if doi:
        return parse_doi(doi)
    if git:
        return parse_location("git", git)
    if calkit:
        return parse_location("calkit", calkit)
    return attested(author or git_author(cwd or Path.cwd()) or "", with_ai)


def declare(path: Path, origin: Origin) -> Artifact:
    """Record a primary artifact and the claim about where it came from."""
    path = Path(path)
    if not path.is_file():
        raise OriginError(f"no such file: {path}")

    store = Store.for_path(path)
    artifact = Artifact(
        path=store.relative(path),
        hash=hash_file(path),
        inputs=[],
        command=None,
        kind="primary",
        origin_kind=origin.kind,
        origin=origin.value,
        origin_revision=origin.revision,
        origin_ai=origin.ai,
    )
    store.record(artifact)
    store.save()
    return artifact
