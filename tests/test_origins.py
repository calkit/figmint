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
    Author,
    OriginError,
    attested,
    looks_like_ai,
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
        assert attested(Author("A Researcher")).verifiable is False

    def test_it_needs_a_name(self):
        with pytest.raises(OriginError, match="needs a name"):
            attested(Author("   "))

    def test_several_authors_are_kept_in_order(self):
        """An artifact rarely has exactly one author, and code almost never
        does."""
        origin = attested(
            Author("First Author"),
            Author("Claude Opus 5", "ai"),
            Author("Second Author"),
        )
        assert origin.describe() == (
            "created by First Author, Claude Opus 5 (AI) and Second Author"
        )

    def test_the_same_author_twice_is_recorded_once(self):
        origin = attested(Author("A Researcher"), Author("a researcher"))
        assert len(origin.authors) == 1


class TestAiDisclosure:
    """A tool is named among the authors, never instead of a person.

    A model can produce a file but cannot answer for one, so accountability and
    disclosure are separate axes: the reader gets both a responsible name and
    how the file was made.
    """

    def test_the_person_and_the_tool_are_both_named(self):
        origin = attested(
            Author("A Researcher"), Author("Claude Opus 5", "ai")
        )
        assert origin.describe() == (
            "created by A Researcher and Claude Opus 5 (AI)"
        )

    def test_it_is_marked_as_machine_generated(self):
        assert attested(
            Author("A Researcher"), Author("Claude Opus 5", "ai")
        ).machine_generated

    def test_a_plain_attestation_is_not(self):
        assert attested(Author("A Researcher")).machine_generated is False

    def test_disclosing_a_tool_does_not_make_it_verifiable(self):
        origin = attested(
            Author("A Researcher"), Author("Claude Opus 5", "ai")
        )
        assert origin.verifiable is False

    def test_a_tool_cannot_be_the_only_author(self):
        """A model can produce a file but cannot answer for one."""
        with pytest.raises(OriginError, match="cannot be accountable"):
            attested(Author("Claude Opus 5", "ai"))

    @pytest.mark.parametrize(
        "name",
        [
            "Claude <noreply@anthropic.com>",
            "GitHub Copilot <copilot@github.com>",
            "Gemini",
        ],
    )
    def test_known_tool_signatures_are_recognized(self, name: str):
        assert looks_like_ai(name)

    def test_a_person_is_not(self):
        assert looks_like_ai("Pete Bachant <petebachant@gmail.com>") is False


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
            resolve_origin(with_ai=["Claude Opus 5"], cwd=tmp_path)

    def test_declaring_on_someone_else_s_behalf_is_enough(
        self, tmp_path: Path
    ):
        origin = resolve_origin(
            authors=["A Researcher"],
            with_ai=["Claude Opus 5"],
            cwd=tmp_path,
        )
        assert [a.name for a in origin.authors] == [
            "A Researcher",
            "Claude Opus 5",
        ]
        assert [a.kind for a in origin.authors] == ["person", "ai"]

    def test_several_of_each_are_accepted(self, tmp_path: Path):
        origin = resolve_origin(
            authors=["First", "Second"],
            with_ai=["Claude Opus 5", "Gemini"],
            cwd=tmp_path,
        )
        assert len(origin.authors) == 4

    def test_a_tool_cannot_be_bolted_onto_a_fetchable_source(
        self, tmp_path: Path
    ):
        with pytest.raises(OriginError, match="belongs on an attestation"):
            resolve_origin(
                doi="10.1/x", with_ai=["Claude Opus 5"], cwd=tmp_path
            )

    def test_saying_nothing_is_refused(self, tmp_path: Path):
        """A file that is both 'mine' and fetched from a DOI is two different
        stories; saying neither is worse."""
        with pytest.raises(OriginError, match="say where it came from"):
            resolve_origin(cwd=tmp_path)

    def test_an_explicit_author_is_recorded(self, tmp_path: Path):
        origin = resolve_origin(authors=["X"], cwd=tmp_path)
        assert [a.name for a in origin.authors] == ["X"]

    @pytest.mark.parametrize("scheme", ["git", "calkit"])
    def test_each_scheme_has_its_own_flag(self, tmp_path: Path, scheme: str):
        origin = resolve_origin(**{scheme: "host/u/p/x.csv@abc"}, cwd=tmp_path)
        assert origin.kind == scheme
        assert origin.revision == "abc"

    def test_an_attestation_carries_the_tool_through(self, tmp_path: Path):
        origin = resolve_origin(
            authors=["X"], with_ai=["Claude Opus 5"], cwd=tmp_path
        )
        assert origin.kind == "attested"
        assert origin.machine_generated


class TestUrls:
    """The one origin figmint checks while making it.

    Being told "this came from that URL" is an attestation; going and looking
    is evidence, and the whole value of the flag is the difference. What it
    records is narrower than a DOI and wider than a claim: *this address served
    exactly these bytes, at this moment*.
    """

    def served(self, monkeypatch, payload: bytes, seen: list | None = None):
        """Stand in for the network, recording what was asked for."""

        def fake_fetch(url: str, destination: Path) -> str:
            if seen is not None:
                seen.append(url)
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).write_bytes(payload)
            return "2026-01-01T00:00:00+00:00"

        monkeypatch.setattr("figmint.declare.fetch", fake_fetch)

    def test_a_missing_file_is_downloaded_and_dated(
        self, tmp_path: Path, monkeypatch
    ):
        seen: list[str] = []
        self.served(monkeypatch, b"x,y\n1,2\n", seen)
        target = tmp_path / "data" / "raw.csv"

        origin = resolve_origin(
            url="https://example.org/raw.csv", path=target, cwd=tmp_path
        )
        assert seen == ["https://example.org/raw.csv"]
        assert target.read_bytes() == b"x,y\n1,2\n"
        assert origin.kind == "url"
        assert origin.value == "https://example.org/raw.csv"
        assert origin.fetched == "2026-01-01T00:00:00+00:00"
        # Fetchable, and still not a deposit: both facts are displayed.
        assert origin.verifiable
        assert origin.mutable
        assert "fetched 2026-01-01" in origin.describe()

    def test_an_existing_file_is_checked_not_overwritten(
        self, tmp_path: Path, monkeypatch
    ):
        self.served(monkeypatch, b"x,y\n1,2\n")
        target = tmp_path / "raw.csv"
        target.write_bytes(b"x,y\n1,2\n")

        assert (
            resolve_origin(
                url="https://example.org/raw.csv", path=target, cwd=tmp_path
            ).kind
            == "url"
        )
        assert target.read_bytes() == b"x,y\n1,2\n"
        # Nothing left lying around wearing a name that looks like the file.
        assert [p.name for p in tmp_path.iterdir()] == ["raw.csv"]

    def test_a_moved_url_is_refused_and_the_file_survives(
        self, tmp_path: Path, monkeypatch
    ):
        """Either the file was edited or the address moved on. Both are worth
        knowing; neither is worth recording as though the download matched."""
        self.served(monkeypatch, b"something else entirely\n")
        target = tmp_path / "raw.csv"
        target.write_bytes(b"x,y\n1,2\n")

        with pytest.raises(OriginError, match="does not serve what is in"):
            resolve_origin(
                url="https://example.org/raw.csv", path=target, cwd=tmp_path
            )
        assert target.read_bytes() == b"x,y\n1,2\n"
        assert [p.name for p in tmp_path.iterdir()] == ["raw.csv"]

    def test_only_http_addresses_are_a_download(self, tmp_path: Path):
        """`--url` records something figmint did. Copying a local file is not
        that, and `--mine` is the claim for it."""
        for address in ("file:///etc/hosts", "/data/raw.csv", "ftp://h/x"):
            with pytest.raises(OriginError, match="not an http"):
                resolve_origin(
                    url=address, path=tmp_path / "raw.csv", cwd=tmp_path
                )

    def test_it_does_not_mix_with_the_other_claims(self, tmp_path: Path):
        with pytest.raises(OriginError, match=r"--mine and --url"):
            resolve_origin(
                mine=True,
                url="https://example.org/x",
                path=tmp_path / "x",
                cwd=tmp_path,
            )
        with pytest.raises(OriginError, match="belongs on an attestation"):
            resolve_origin(
                with_ai=["Claude Opus 5"],
                url="https://example.org/x",
                path=tmp_path / "x",
                cwd=tmp_path,
            )

    def test_the_record_keeps_the_url_and_the_date(
        self, tmp_path: Path, monkeypatch
    ):
        """Round-tripped through figmint.toml, because a timestamp that does not
        survive being written down is not a record of anything."""
        from figmint.declare import declare
        from figmint.store import Store

        (tmp_path / ".git").mkdir()
        self.served(monkeypatch, b"x,y\n1,2\n")
        target = tmp_path / "raw.csv"
        origin = resolve_origin(
            url="https://example.org/raw.csv", path=target, cwd=tmp_path
        )
        declare(target, origin)

        stored = Store.load(tmp_path).artifacts["raw.csv"]
        assert stored.origin_kind == "url"
        assert stored.origin == "https://example.org/raw.csv"
        assert stored.origin_fetched == "2026-01-01T00:00:00+00:00"
        assert "downloaded from https://example.org/raw.csv" in (
            stored.origin_description()
        )


class TestFetching:
    """The download itself, against a real socket.

    Mocked everywhere else, so this is the only place that would notice
    urllib being handed a `Request` it does not like, or a partial write being
    left behind under the destination's name.
    """

    def serve(self, tmp_path: Path, payload: bytes, status: int = 200):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - required by the base class
                if status != 200:
                    self.send_error(status)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, f"http://127.0.0.1:{server.server_port}/x.csv"

    def test_it_downloads_and_stamps(self, tmp_path: Path):
        from figmint.origins import fetch

        server, url = self.serve(tmp_path, b"x,y\n1,2\n")
        try:
            target = tmp_path / "nested" / "raw.csv"
            stamp = fetch(url, target)
        finally:
            server.shutdown()

        assert target.read_bytes() == b"x,y\n1,2\n"
        # An ISO instant in UTC, which is what the record stores.
        assert stamp.endswith("+00:00")
        assert [p.name for p in target.parent.iterdir()] == ["raw.csv"]

    def test_an_http_error_says_which(self, tmp_path: Path):
        from figmint.origins import fetch

        server, url = self.serve(tmp_path, b"", status=404)
        try:
            with pytest.raises(OriginError, match="404"):
                fetch(url, tmp_path / "raw.csv")
        finally:
            server.shutdown()
        # Nothing written, and no partial file wearing the real name.
        assert list(tmp_path.iterdir()) == []
