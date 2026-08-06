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
    fetch,
    git_author,
    git_authors_for,
    parse_doi,
    parse_location,
)
from .store import Artifact, Store, hash_file


def fetch_origin(url: str, path: Path) -> Origin:
    """Download a URL and record that it happened, at a time, to these bytes.

    This is the one origin figmint *checks* while making it, and the two cases
    read differently:

    * **The file is not there yet.** figmint downloads it. The record then says
      where it came from because figmint fetched it, not because anyone said so.
    * **The file is already there.** figmint fetches anyway and compares. Being
      told "this came from that URL" is an attestation; going and looking is
      evidence, and the difference is the entire point of the flag.

    A mismatch is refused rather than reconciled. Either the file was edited
    after it was downloaded or the address has moved on, and both are things a
    reader needs told — quietly overwriting the file would destroy the first and
    quietly recording the URL would falsify the second.
    """
    path = Path(path)
    if not path.is_file():
        stamp = fetch(url, path)
        return Origin(kind="url", value=url, fetched=stamp)

    existing = hash_file(path)
    scratch = path.with_name(path.name + ".figmint-check")
    try:
        stamp = fetch(url, scratch)
        downloaded = hash_file(scratch)
    finally:
        scratch.unlink(missing_ok=True)

    if downloaded != existing:
        raise OriginError(
            f"{url} does not serve what is in {path.name}.\n"
            f"   on disk:   {existing}\n"
            f"   downloaded: {downloaded}\n"
            f"Either the file was changed after it was downloaded, or the "
            f"address has moved on. Both are worth knowing; neither is worth "
            f"recording as though the download matched."
        )
    return Origin(kind="url", value=url, fetched=stamp)


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
    url: str | None = None,
    cwd: Path | None = None,
) -> Origin:
    """Turn the command-line options into one claim.

    Exactly one, deliberately. A file that is both "mine" and fetched from a DOI
    is two different stories, and a record that holds both says neither.

    `--url` is the one branch that touches the network: it does not merely parse
    a claim, it goes and checks it. That impurity is the feature, and it is here
    rather than hidden behind the record so a failed or mismatched download stops
    the command instead of being written down.
    """
    authors = list(authors or [])
    with_ai = list(with_ai or [])
    attesting = bool(mine or authors or with_ai or from_git)
    sourced = bool(doi or git or calkit or url)

    claims = {
        "--mine": attesting,
        "--doi": bool(doi),
        "--git": bool(git),
        "--calkit": bool(calkit),
        "--url": bool(url),
    }
    given = [flag for flag, present in claims.items() if present]

    # The attestation flags produce their own errors first: they say something
    # specific about what is wrong, and "say where it came from" would be
    # technically true but useless to someone who just told us exactly that.
    if with_ai and sourced:
        raise OriginError(
            "`--with-ai` belongs on an attestation. A published, "
            "revision-pinned, or fetched source already says where the file "
            "came from."
        )
    if from_git and sourced:
        raise OriginError(
            "`--from-git-history` reads the authors of a file in this project. "
            "A published, revision-pinned, or fetched source already says "
            "where it came from."
        )

    if not given:
        raise OriginError(
            "say where it came from: `--mine` if you produced it, `--doi` if it "
            "is published, `--url` if it was downloaded, or `--git`/`--calkit` "
            "with a `location@rev` if it lives in another project."
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
    if url:
        if path is None:
            raise OriginError("`--url` needs a path to download to")
        return fetch_origin(url, path)

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
        origin_fetched=origin.fetched,
        authors=list(origin.authors),
    )
    store.record(artifact)
    store.save()
    return artifact
