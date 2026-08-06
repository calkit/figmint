"""Where a primary artifact came from.

Every chain ends somewhere. Follow a figure back far enough and you reach a file
nothing in the project produced — measurements from an instrument, a dataset
downloaded from a repository, a photograph. `figmint run` cannot record those,
because figmint was not there when they were made.

Left alone, that is the hole in the middle of an otherwise checkable record: a
figure that is `reproducible` in every link, resting on a CSV that appeared one
day. Every automated check upstream of it passes, which is exactly what makes it
easy to miss.

So a primary artifact has to be *declared*, and the declaration says which kind
of claim it is:

  * **`--mine`** — an attestation. "I produced this myself." Nothing verifies it
    and nothing can; what it does is put a name against the claim, so it is on
    the record rather than assumed.
  * **`--author <name>`** / **`--with-ai <tool>`** — who made it. Both repeat,
    because an artifact rarely has exactly one author and code almost never
    does: a script grows through several hands and, increasingly, several
    models. The two flags differ only in what they mark the author as, and at
    least one human is required — a model can produce a file but it cannot
    answer for one, so an agent declaring its own output still names the people
    it worked for.
  * **`--from-git-history`** — read the author list out of the repository
    instead of retyping it. Git already records this: every commit touching the
    file names an author, and `Co-authored-by:` trailers name everyone else,
    which is exactly where an AI agent's own signature lands. Deriving the list
    from history means it matches what actually happened rather than what
    somebody remembered at declaration time.
  * **`--doi`** — a published, immutable source. The strongest form here,
    because a DOI resolves to something a reader can fetch.
  * **`--url`** — a plain web address, and the one claim figmint checks as it
    makes it. figmint fetches the URL and compares what came back against the
    file on disk, so the record says *this address served exactly these bytes,
    at this moment*. That is weaker than a DOI and stronger than an attestation:
    the download really happened and figmint watched it, but a URL is mutable
    and may serve something else tomorrow. The timestamp is what makes the claim
    mean anything a year later.
  * **`--git <location@rev>`** / **`--calkit <location@rev>`** — a
    revision-pinned location:

        --git    github.com/user/project/path/to/data.csv@a1b2c3d
        --calkit calkit.io/user/project/path/to/data.csv@a1b2c3d

    The `--calkit` form exists because Calkit tracks large files with DVC, so a
    path in a Calkit project can name data that is not in the git tree at all.

A revision is required on both URI forms, and that is the whole reason they are
shaped this way. `github.com/user/project/data.csv` names whatever is at that
path today — a mutable claim wearing the costume of a citation. Pinning it to a
commit makes it mean something a year later.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: `host/owner/project/path@rev`. The scheme comes from which flag was used, so
#: it is not repeated here — `--git git:github.com/...` would be noise.
_LOCATION = re.compile(r"^(?P<location>[^@\s]+)@(?P<rev>[^@\s]+)$")

#: A DOI, in the shape the registry actually issues.
_DOI = re.compile(
    r"^(?:doi:|https?://doi\.org/)?(10\.\d{4,}/\S+)$", re.IGNORECASE
)

SCHEMES = ("git", "calkit")


#: Signatures that mark an author as a generative tool rather than a person.
#: Deliberately a short, explicit, auditable list rather than clever detection:
#: this is a guess, it is wrong sometimes, and both `figmint declare` and the
#: rendered panel show what it decided so a wrong guess is visible and can be
#: overridden with `--author`/`--with-ai`.
AI_SIGNATURES = (
    "noreply@anthropic.com",
    "claude",
    "chatgpt",
    "openai.com",
    "gpt-4",
    "gpt-5",
    "copilot",
    "gemini",
    "codex",
    "devin",
    "cursor",
)


class OriginError(ValueError):
    """Raised when a declared origin cannot be understood."""


@dataclass(frozen=True)
class Author:
    """One party responsible for an artifact."""

    name: str
    #: `person` or `ai`. Kept as a field rather than inferred at display time so
    #: that a correction made once, at declaration, stays corrected.
    kind: str = "person"

    @property
    def is_ai(self) -> bool:
        return self.kind == "ai"

    def describe(self) -> str:
        return f"{self.name} (AI)" if self.is_ai else self.name

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "kind": self.kind}


def looks_like_ai(name: str) -> bool:
    """Whether an author signature belongs to a generative tool.

    A heuristic, and named like one. See `AI_SIGNATURES`.
    """
    lowered = name.lower()
    return any(signature in lowered for signature in AI_SIGNATURES)


@dataclass(frozen=True)
class Origin:
    """A claim about where a primary artifact came from."""

    #: `attested`, `doi`, `git`, `calkit`, or `url`.
    kind: str
    #: The claim itself, normalized.
    value: str
    #: Revision, for the URI forms.
    revision: str | None = None
    #: Everyone who made it, in the order they appear. Always contains at least
    #: one person for an attestation: a tool cannot be accountable for a file.
    authors: tuple[Author, ...] = ()
    #: When figmint fetched it, for `url`. Not decoration: an address without a
    #: date is a claim about nothing in particular, because what it serves can
    #: change the day after it is written down.
    fetched: str | None = None

    def describe(self) -> str:
        if self.kind == "attested":
            return f"created by {describe_authors(self.authors)}"
        if self.kind == "doi":
            return f"https://doi.org/{self.value}"
        if self.kind == "url":
            return f"{self.value} (fetched {self.fetched})"
        return f"{self.kind}:{self.value}@{self.revision}"

    @property
    def verifiable(self) -> bool:
        """Whether a reader could, in principle, go and fetch it.

        An attestation cannot be checked by anyone — it is a claim, and saying
        so plainly is the point. The others name something retrievable.
        """
        return self.kind != "attested"

    @property
    def mutable(self) -> bool:
        """Whether what it names can change without the record noticing.

        A DOI resolves to a deposit; a pinned revision names a commit. A bare
        URL names whatever is served today, so a reader who follows it a year
        from now may be looking at something else entirely — and the panel says
        so rather than letting the address pass for a citation.
        """
        return self.kind == "url"

    @property
    def machine_generated(self) -> bool:
        return any(a.is_ai for a in self.authors)


def parse_location(scheme: str, value: str) -> Origin:
    """Read a `host/owner/project/path@rev` location, insisting on a revision."""
    if scheme not in SCHEMES:
        raise OriginError(f"unknown origin scheme: {scheme}")

    cleaned = value.strip()
    # Tolerate a pasted `git:` prefix rather than rejecting it: the flag already
    # said which scheme this is, and complaining about a redundant prefix helps
    # nobody.
    if cleaned.startswith(f"{scheme}:"):
        cleaned = cleaned[len(scheme) + 1 :]

    match = _LOCATION.match(cleaned)
    if match is None:
        raise OriginError(
            f"`{value}` names no revision. A location without one points at "
            f"whatever is there today, which is not a claim that survives — "
            f"append `@<git-rev>`."
        )
    return Origin(
        kind=scheme,
        value=match.group("location"),
        revision=match.group("rev"),
    )


#: Seconds to wait on a download before giving up. Long enough for a large
#: dataset on a slow link, short enough that a hung server does not stall a
#: build forever.
FETCH_TIMEOUT = 60

CHUNK = 1 << 20


def fetch(url: str, destination: Path) -> str:
    """Download a URL to a file, and say when.

    stdlib only, deliberately: figmint's whole job is to be the thing you can
    still run in five years, and a provenance tool that pulls an HTTP stack in
    to download a CSV has made itself harder to trust than the claim it records.

    Written through a temporary file so an interrupted download cannot leave a
    truncated one wearing a recorded hash — the single worst outcome available
    here, because it would look exactly like a successful fetch.
    """
    import shutil
    import urllib.error
    import urllib.request
    from datetime import datetime, timezone
    from urllib.parse import urlparse

    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise OriginError(
            f"`{url}` is not an http(s) address. `--url` records a download "
            f"figmint performed; a local path is not one, and `--mine` is the "
            f"claim you are looking for."
        )

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".figmint-download")
    # A default `User-Agent` of `Python-urllib/3.x` is refused outright by a
    # number of data repositories, which surfaces as a 403 that looks like a
    # permissions problem rather than a politeness one.
    request = urllib.request.Request(url, headers={"User-Agent": "figmint"})
    try:
        with urllib.request.urlopen(
            request, timeout=FETCH_TIMEOUT
        ) as response:
            with temporary.open("wb") as handle:
                shutil.copyfileobj(response, handle, CHUNK)
    except urllib.error.HTTPError as exc:
        temporary.unlink(missing_ok=True)
        raise OriginError(f"{url} returned {exc.code} {exc.reason}") from exc
    except (urllib.error.URLError, OSError) as exc:
        temporary.unlink(missing_ok=True)
        raise OriginError(f"could not fetch {url}: {exc}") from exc

    temporary.replace(destination)
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_doi(value: str) -> Origin:
    match = _DOI.match(value.strip())
    if match is None:
        raise OriginError(
            f"`{value}` is not a DOI. A DOI looks like `10.5281/zenodo.1234567`."
        )
    return Origin(kind="doi", value=match.group(1))


def describe_authors(authors: Sequence[Author]) -> str:
    """An author list as a reader should see it."""
    names = [a.describe() for a in authors]
    if not names:
        return "nobody"
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def dedupe(authors: Iterable[Author]) -> tuple[Author, ...]:
    """First mention wins, so an explicit `--author` beats a later guess."""
    seen: dict[str, Author] = {}
    for author in authors:
        key = author.name.strip().lower()
        if key and key not in seen:
            seen[key] = Author(author.name.strip(), author.kind)
    return tuple(seen.values())


def attested(*authors: Author) -> Origin:
    """An "I made this" claim, attributed to the people who made it.

    Several people, because an artifact rarely has exactly one author and code
    almost never does. At least one of them must be a person: a model can
    produce a file but it cannot answer for one, so there is always somebody on
    the record beside it.
    """
    collected = dedupe(authors)
    if not collected:
        raise OriginError(
            "an attestation needs a name. Pass `--author`, or set user.name in "
            "git, so the claim is attributable to someone."
        )
    if not any(not a.is_ai for a in collected):
        raise OriginError(
            "every author here is a generative tool, and a tool cannot be "
            "accountable for a file. Name the person who is, with `--author`."
        )
    return Origin(kind="attested", value="", authors=collected)


def git_authors_for(path: Path, cwd: Path) -> list[Author]:
    """Everyone git has seen touch a file.

    Both the commit authors and the `Co-authored-by:` trailers, because the
    trailer is where a second pair of hands is recorded — and, increasingly,
    where an AI agent signs its own contribution. Reading it back out means the
    declaration matches what happened rather than what somebody remembered.

    `--follow` so a rename does not amputate the history.
    """
    import subprocess

    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--follow",
                "--format=%an <%ae>%n%(trailers:key=Co-authored-by,valueonly=true)",
                "--",
                str(path),
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            # git speaks UTF-8; `text=True` alone would decode with the
            # locale codepage and mangle a name on its way into the record.
            encoding="utf-8",
        )
    except (FileNotFoundError, OSError):
        return []
    if result.returncode != 0:
        return []

    found = [
        Author(line, "ai" if looks_like_ai(line) else "person")
        for line in (raw.strip() for raw in result.stdout.splitlines())
        if line
    ]
    # Oldest first: an author list reads as the order people arrived, and
    # `git log` hands them back newest first.
    return list(dedupe(reversed(found)))


def git_author(cwd: Path) -> str | None:
    """Whoever git thinks is working here, as a default attribution."""
    import subprocess

    try:
        name = subprocess.run(
            ["git", "config", "user.name"],
            cwd=cwd,
            capture_output=True,
            text=True,
            # git speaks UTF-8; `text=True` alone would decode with the
            # locale codepage and mangle a name on its way into the record.
            encoding="utf-8",
        ).stdout.strip()
        email = subprocess.run(
            ["git", "config", "user.email"],
            cwd=cwd,
            capture_output=True,
            text=True,
            # git speaks UTF-8; `text=True` alone would decode with the
            # locale codepage and mangle a name on its way into the record.
            encoding="utf-8",
        ).stdout.strip()
    except (FileNotFoundError, OSError):
        return None
    if not name:
        return None
    return f"{name} <{email}>" if email else name


# ---------------------------------------------------------------------------
# A note on signing declarations
# ---------------------------------------------------------------------------
#
# An earlier design signed each declared artifact with a C2PA sidecar, since a
# CSV cannot carry an embedded manifest. It was dropped.
#
# The declaration already lives in `figmint.toml` next to the artifact's hash,
# so altering the file is detectable from the record alone. A sidecar would put
# the same claim in a second place, and it would be exactly as easy to delete as
# the line it duplicates — ceremony that looks like a cryptographic guarantee
# without being one.
#
# The honest limitation, stated rather than papered over: `figmint.toml` is
# plain text, and its header is a social deterrent, not a cryptographic one.
# Making declarations tamper-evident means signing *the record*, once, rather
# than scattering signatures across the files it describes. That is a real piece
# of work and it is not done yet.
