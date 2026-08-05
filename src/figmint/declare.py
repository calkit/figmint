"""`figmint declare` — put a primary artifact's origin on the record.

`figmint run` records what figmint watched being made. This records what it did
not: the measurements, the downloaded dataset, the image somebody was handed.
Without it those files sit at the bottom of the chain unexplained, and every
check above them passes — which is what makes the gap easy to miss.
"""

from __future__ import annotations

from pathlib import Path

from .origins import (
    Author,
    Origin,
    OriginError,
    attested,
    git_author,
    git_authors_for,
    parse_doi,
    parse_location,
)
from .store import Artifact, Store, hash_file


def resolve_origin(
    *,
    mine: bool = False,
    authors: list[str] | None = None,
    with_ai: list[str] | None = None,
    from_git: bool = False,
    path: Path | None = None,
    doi: str | None = None,
    git: str | None = None,
    calkit: str | None = None,
    cwd: Path | None = None,
) -> Origin:
    """Turn the command-line options into one claim.

    Exactly one, deliberately. A file that is both "mine" and fetched from a DOI
    is two different stories, and a record that holds both says neither.
    """
    authors = list(authors or [])
    with_ai = list(with_ai or [])
    attesting = bool(mine or authors or with_ai or from_git)

    claims = {
        "--mine": attesting,
        "--doi": bool(doi),
        "--git": bool(git),
        "--calkit": bool(calkit),
    }
    given = [flag for flag, present in claims.items() if present]

    # The attestation flags produce their own errors first: they say something
    # specific about what is wrong, and "say where it came from" would be
    # technically true but useless to someone who just told us exactly that.
    if with_ai and (doi or git or calkit):
        raise OriginError(
            "`--with-ai` belongs on an attestation. A published or "
            "revision-pinned source already says where the file came from."
        )
    if from_git and (doi or git or calkit):
        raise OriginError(
            "`--from-git-history` reads the authors of a file in this project. "
            "A published or revision-pinned source already says where it "
            "came from."
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

    # Explicit names first, so a hand-written `--author` wins over whatever the
    # history heuristic decided about the same person.
    collected = [Author(name, "person") for name in authors]
    collected += [Author(name, "ai") for name in with_ai]
    if from_git:
        if path is None:
            raise OriginError(
                "`--from-git-history` needs a file to read the history of"
            )
        found = git_authors_for(path, cwd or Path.cwd())
        if not found:
            raise OriginError(
                f"git knows nothing about {Path(path).name} — it has no commits "
                f"yet, or is not in a repository. Name the authors with "
                f"`--author`/`--with-ai` instead."
            )
        collected += found
    if mine and not collected:
        collected.append(Author(git_author(cwd or Path.cwd()) or "", "person"))
    elif mine:
        # `--mine` alongside other names means "and me", not "only me".
        collected.insert(
            0, Author(git_author(cwd or Path.cwd()) or "", "person")
        )
    return attested(*[a for a in collected if a.name])


def declare(path: Path, origin: Origin) -> Artifact:
    """Record who is answerable for an artifact, and where it came from.

    Two shapes of file end up here. Most are *primary*: measurements, a
    downloaded dataset, an image somebody was handed — nothing in the project
    produced them, and without a declaration they sit at the bottom of the chain
    unexplained.

    The other is an artifact figmint already tracks that was nonetheless made by
    hand: a `.drawio` canvas is assembled from recorded panels but arranged by
    people, and increasingly by people and agents together. Those already have
    inputs, and a declaration must *add* authorship to them rather than replace
    what is known — overwriting the inputs would trade a real derivation chain
    for a name.
    """
    path = Path(path)
    if not path.is_file():
        raise OriginError(f"no such file: {path}")

    store = Store.for_path(path)
    key = store.relative(path)
    existing = store.artifacts.get(key)

    artifact = Artifact(
        path=key,
        hash=hash_file(path),
        inputs=existing.inputs if existing else [],
        command=existing.command if existing else None,
        # An artifact nothing produced is primary. One that already has a record
        # keeps whatever it was; declaring authors does not change how it was
        # made, only who answers for it.
        kind=existing.kind if existing else "primary",
        origin_kind=origin.kind,
        origin=origin.value or None,
        origin_revision=origin.revision,
        authors=list(origin.authors),
    )
    store.record(artifact)
    store.save()
    return artifact
