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
  * **`--with-ai <tool>`** — a modifier on an attestation, not a claim of its
    own. A model can produce a file but it cannot answer for one, so the
    accountable party is always the person attesting; the tool is disclosed
    alongside them rather than in place of them. "Pete Bachant, with Claude
    Opus 5" says who is responsible *and* how the file was made, which is what
    a reader needs to judge whether that use of generative AI is acceptable
    here. An agent declaring its own output still names the human it worked
    for.
  * **`--doi`** — a published, immutable source. The strongest form here,
    because a DOI resolves to something a reader can fetch.
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
from dataclasses import dataclass

#: `host/owner/project/path@rev`. The scheme comes from which flag was used, so
#: it is not repeated here — `--git git:github.com/...` would be noise.
_LOCATION = re.compile(r"^(?P<location>[^@\s]+)@(?P<rev>[^@\s]+)$")

#: A DOI, in the shape the registry actually issues.
_DOI = re.compile(
    r"^(?:doi:|https?://doi\.org/)?(10\.\d{4,}/\S+)$", re.IGNORECASE
)

SCHEMES = ("git", "calkit")


class OriginError(ValueError):
    """Raised when a declared origin cannot be understood."""


@dataclass(frozen=True)
class Origin:
    """A claim about where a primary artifact came from."""

    #: `attested`, `doi`, `git`, or `calkit`.
    kind: str
    #: The claim itself, normalised.
    value: str
    #: Revision, for the URI forms.
    revision: str | None = None
    #: The generative tool used, when the person attesting used one. Never
    #: stands alone: a tool cannot be accountable for a file.
    ai: str | None = None

    def describe(self) -> str:
        if self.kind == "attested":
            if self.ai:
                return f"created by {self.value} with {self.ai}"
            return f"created by {self.value}"
        if self.kind == "doi":
            return f"https://doi.org/{self.value}"
        return f"{self.kind}:{self.value}@{self.revision}"

    @property
    def verifiable(self) -> bool:
        """Whether a reader could, in principle, go and fetch it.

        An attestation cannot be checked by anyone — it is a claim, and saying
        so plainly is the point. The others name something retrievable.
        """
        return self.kind != "attested"

    @property
    def machine_generated(self) -> bool:
        return bool(self.ai)


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


def parse_doi(value: str) -> Origin:
    match = _DOI.match(value.strip())
    if match is None:
        raise OriginError(
            f"`{value}` is not a DOI. A DOI looks like `10.5281/zenodo.1234567`."
        )
    return Origin(kind="doi", value=match.group(1))


def attested(author: str, ai: str | None = None) -> Origin:
    """An "I made this" claim, attributed to somebody.

    `ai` names a generative tool the author used. It is deliberately a field on
    the attestation rather than an origin of its own: a model cannot answer for
    a file, so there is always a person on the record beside it.
    """
    if not author or not author.strip():
        raise OriginError(
            "an attestation needs a name. Pass `--author`, or set user.name in "
            "git, so the claim is attributable to someone."
        )
    if ai is not None and not ai.strip():
        raise OriginError(
            "say which tool, e.g. `--with-ai 'Claude Opus 5'`, so a reader can "
            "judge whether that is an acceptable use here."
        )
    return Origin(
        kind="attested",
        value=author.strip(),
        ai=ai.strip() if ai else None,
    )


def git_author(cwd) -> str | None:
    """Whoever git thinks is working here, as a default attribution."""
    import subprocess

    try:
        name = subprocess.run(
            ["git", "config", "user.name"],
            cwd=cwd,
            capture_output=True,
            text=True,
        ).stdout.strip()
        email = subprocess.run(
            ["git", "config", "user.email"],
            cwd=cwd,
            capture_output=True,
            text=True,
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
