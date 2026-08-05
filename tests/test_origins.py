"""Declared origins, and the revision requirement.

The URI forms exist to make a claim that survives. `github.com/u/p/data.csv`
names whatever is at that path today — a mutable claim wearing the costume of a
citation — so a revision is not optional.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from figmint.declare import resolve_origin
from figmint.origins import (
    OriginError,
    attested,
    parse_doi,
    parse_location,
)


class TestLocations:
    def test_a_git_location_is_parsed(self):
        origin = parse_location(
            "git", "github.com/user/project/data/raw.csv@a1b2c3d"
        )
        assert origin.kind == "git"
        assert origin.value == "github.com/user/project/data/raw.csv"
        assert origin.revision == "a1b2c3d"
        assert origin.verifiable

    def test_a_calkit_location_is_parsed(self):
        """The calkit form exists because Calkit tracks large files with DVC, so
        a path there can name data that is not in the git tree at all."""
        origin = parse_location(
            "calkit", "calkit.io/user/project/data/raw.csv@a1b2c3d"
        )
        assert origin.kind == "calkit"
        assert origin.revision == "a1b2c3d"

    def test_a_redundant_scheme_prefix_is_tolerated(self):
        """The flag already said which scheme this is. Rejecting a pasted
        `git:` prefix would be pedantry that helps nobody."""
        assert (
            parse_location(
                "git", "git:github.com/user/project/data/raw.csv@a1b2c3d"
            ).value
            == "github.com/user/project/data/raw.csv"
        )

    def test_a_location_without_a_revision_is_refused(self):
        with pytest.raises(OriginError, match="names no revision"):
            parse_location("git", "github.com/user/project/data/raw.csv")

    def test_the_full_uri_round_trips_in_the_description(self):
        origin = parse_location(
            "git", "github.com/user/project/raw.csv@a1b2c3d"
        )
        assert (
            origin.describe() == "git:github.com/user/project/raw.csv@a1b2c3d"
        )


class TestDois:
    @pytest.mark.parametrize(
        "value",
        [
            "10.5281/zenodo.1234567",
            "doi:10.5281/zenodo.1234567",
            "https://doi.org/10.5281/zenodo.1234567",
        ],
    )
    def test_the_usual_spellings_are_accepted(self, value: str):
        assert parse_doi(value).value == "10.5281/zenodo.1234567"

    def test_something_that_is_not_a_doi_is_refused(self):
        with pytest.raises(OriginError, match="not a DOI"):
            parse_doi("zenodo.1234567")


class TestAttestation:
    def test_it_is_not_verifiable_and_says_so(self):
        """Saying it plainly is the point: an attestation is the weakest thing
        in the record and should not read like the others."""
        assert attested("A Researcher").verifiable is False

    def test_it_needs_a_name(self):
        with pytest.raises(OriginError, match="needs a name"):
            attested("   ")


class TestAiDisclosure:
    """A tool is disclosed beside the person attesting, never instead of them.

    A model can produce a file but cannot answer for one, so accountability and
    disclosure are separate axes: the reader gets both a responsible name and
    how the file was made.
    """

    def test_the_person_and_the_tool_are_both_named(self):
        origin = attested("A Researcher", "Claude Opus 5")
        assert (
            origin.describe() == "created by A Researcher with Claude Opus 5"
        )

    def test_it_is_marked_as_machine_generated(self):
        assert attested("A Researcher", "Claude Opus 5").machine_generated

    def test_a_plain_attestation_is_not(self):
        assert attested("A Researcher").machine_generated is False

    def test_disclosing_a_tool_does_not_make_it_verifiable(self):
        assert attested("A Researcher", "Claude Opus 5").verifiable is False

    def test_it_needs_to_name_the_tool(self):
        """ "Made with AI" without saying which tells a reader nothing they can
        weigh."""
        with pytest.raises(OriginError, match="say which tool"):
            attested("A Researcher", "  ")


class TestResolution:
    def test_exactly_one_claim_is_required(self, tmp_path: Path):
        with pytest.raises(OriginError, match="exactly one"):
            resolve_origin(mine=True, doi="10.1/x", cwd=tmp_path)

    def test_the_conflicting_flags_are_named(self, tmp_path: Path):
        with pytest.raises(OriginError, match=r"--mine and --doi"):
            resolve_origin(mine=True, doi="10.1/x", cwd=tmp_path)

    def test_a_tool_cannot_be_accountable_on_its_own(self, tmp_path: Path):
        """The whole reason `--with-ai` is a modifier. An agent declaring its
        own output still has to name the person it was working for."""
        with pytest.raises(OriginError, match="cannot be accountable"):
            resolve_origin(with_ai="Claude Opus 5", cwd=tmp_path)

    def test_declaring_on_someone_else_s_behalf_is_enough(
        self, tmp_path: Path
    ):
        origin = resolve_origin(
            author="A Researcher", with_ai="Claude Opus 5", cwd=tmp_path
        )
        assert origin.value == "A Researcher"
        assert origin.ai == "Claude Opus 5"

    def test_a_tool_cannot_be_bolted_onto_a_fetchable_source(
        self, tmp_path: Path
    ):
        with pytest.raises(OriginError, match="belongs on an attestation"):
            resolve_origin(doi="10.1/x", with_ai="Claude Opus 5", cwd=tmp_path)

    def test_saying_nothing_is_refused(self, tmp_path: Path):
        """A file that is both 'mine' and fetched from a DOI is two different
        stories; saying neither is worse."""
        with pytest.raises(OriginError, match="say where it came from"):
            resolve_origin(cwd=tmp_path)

    def test_an_explicit_author_wins(self, tmp_path: Path):
        assert resolve_origin(mine=True, author="X", cwd=tmp_path).value == "X"

    @pytest.mark.parametrize("scheme", ["git", "calkit"])
    def test_each_scheme_has_its_own_flag(self, tmp_path: Path, scheme: str):
        origin = resolve_origin(**{scheme: "host/u/p/x.csv@abc"}, cwd=tmp_path)
        assert origin.kind == scheme
        assert origin.revision == "abc"

    def test_an_attestation_carries_the_tool_through(self, tmp_path: Path):
        origin = resolve_origin(
            mine=True, author="X", with_ai="Claude Opus 5", cwd=tmp_path
        )
        assert origin.kind == "attested"
        assert origin.machine_generated
