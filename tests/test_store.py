"""The provenance record itself.

`provenance.toml` is evidence, and the tests that matter most here are about it
staying that way: the header has to be present, the round-trip has to be exact,
and a partial write must not leave something that reads as "no provenance".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fromwhere.store import (
    HEADER,
    LEGACY_STORE_NAME,
    STORE_NAME,
    Artifact,
    Input,
    Store,
    StoreError,
    find_root,
    hash_file,
    project_root,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "figures").mkdir()
    (tmp_path / "data" / "raw.csv").write_text("x,y\n1,2\n")
    (tmp_path / "figures" / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\nplot")
    (tmp_path / "uv.lock").write_text("lock")
    return tmp_path


def recorded(project: Path) -> Store:
    store = Store.load(project)
    store.record(
        Artifact(
            path="figures/plot.png",
            hash=hash_file(project / "figures/plot.png"),
            command="uv run plot.py",
            inputs=[
                Input("data/raw.csv", hash_file(project / "data/raw.csv")),
                Input(
                    "uv.lock",
                    hash_file(project / "uv.lock"),
                    kind="environment",
                ),
            ],
        )
    )
    return store


class TestHeader:
    def test_the_warning_is_written(self, project: Path):
        """The header is the only thing between a reader and a forged chain.

        An agent that "fixes" a hash mismatch by editing the hash destroys
        exactly what the file is for, so the file has to say so itself — a
        convention documented elsewhere would not travel with it.
        """
        recorded(project).save()
        text = (project / STORE_NAME).read_text(encoding="utf-8")
        assert text.startswith(HEADER)
        assert "DO NOT EDIT" in text
        assert "NOTE TO AI AGENTS" in text
        assert "falsifying that evidence" in text

    def test_the_header_survives_a_rewrite(self, project: Path):
        store = recorded(project)
        store.save()
        Store.load(project).save()
        assert (
            (project / STORE_NAME)
            .read_text(encoding="utf-8")
            .startswith(HEADER)
        )


class TestRoundTrip:
    def test_an_artifact_round_trips(self, project: Path):
        recorded(project).save()
        artifact = Store.load(project).get("figures/plot.png")
        assert artifact is not None
        assert artifact.command == "uv run plot.py"
        assert [(i.path, i.kind) for i in artifact.inputs] == [
            ("data/raw.csv", "file"),
            ("uv.lock", "environment"),
        ]

    def test_hashes_name_their_algorithm(self, project: Path):
        """`sha256:` prefixed, so the algorithm is never implicit."""
        assert hash_file(project / "uv.lock").startswith("sha256:")

    def test_the_environment_kind_is_kept(self, project: Path):
        """It answers a different question — not what this depended on, but
        what it was computed with — and it is the input a reader is least
        likely to have thought about."""
        recorded(project).save()
        artifact = Store.load(project).get("figures/plot.png")
        assert any(i.kind == "environment" for i in artifact.inputs)

    def test_paths_with_separators_are_quoted_keys(self, project: Path):
        recorded(project).save()
        assert '[artifact."figures/plot.png"]' in (
            project / STORE_NAME
        ).read_text(encoding="utf-8")

    def test_output_is_sorted(self, project: Path):
        """So a diff shows what changed rather than what moved."""
        store = recorded(project)
        store.record(Artifact(path="a.png", hash="sha256:aa"))
        store.record(Artifact(path="z.png", hash="sha256:zz"))
        store.save()
        text = (project / STORE_NAME).read_text(encoding="utf-8")
        assert (
            text.index('[artifact."a.png"]')
            < text.index('[artifact."figures/plot.png"]')
            < text.index('[artifact."z.png"]')
        )

    def test_a_dot_in_a_key_is_quoted(self, project: Path):
        """TOML reads a bare `a.png` as nested tables, which would silently
        restructure the record."""
        store = Store.load(project)
        store.record(Artifact(path="a.png", hash="sha256:aa"))
        store.save()
        assert Store.load(project).get("a.png") is not None

    def test_an_unreadable_record_raises(self, project: Path):
        (project / STORE_NAME).write_text("this is not [ toml")
        with pytest.raises(StoreError):
            Store.load(project)


class TestLocation:
    def test_a_record_is_found_from_a_subdirectory(self, project: Path):
        recorded(project).save()
        assert find_root(project / "figures") == project

    def test_a_fresh_project_uses_the_vcs_root(self, project: Path):
        """Not the directory of whichever file happened to be produced first."""
        assert project_root(project / "figures" / "plot.png") == project

    def test_paths_are_recorded_relative_to_the_root(self, project: Path):
        store = Store.load(project)
        assert (
            store.relative(project / "figures" / "plot.png")
            == "figures/plot.png"
        )

    def test_an_input_outside_the_project_is_kept_not_dropped(
        self, project: Path, tmp_path: Path
    ):
        """Silently omitting it would make the chain look complete."""
        outside = tmp_path.parent / "elsewhere.csv"
        store = Store.load(project)
        recorded_path = store.relative(outside)
        # Absolute rather than "starts with a slash": on Windows it is a drive
        # path, `C:/Users/...`, which `Path.is_absolute` recognizes and a
        # leading-separator check does not.
        assert Path(recorded_path).is_absolute()
        assert recorded_path.endswith("elsewhere.csv")


class TestDurability:
    def test_a_failed_write_leaves_the_previous_record(
        self, project: Path, monkeypatch
    ):
        """A truncated record reads as "no provenance" rather than as a problem,
        so the write goes through a temporary file."""
        store = recorded(project)
        store.save()
        original = (project / STORE_NAME).read_text(encoding="utf-8")

        def boom(self, *args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        with pytest.raises(OSError):
            store.save()
        assert (project / STORE_NAME).read_text(encoding="utf-8") == original

    def test_no_temporary_file_is_left_behind(self, project: Path):
        recorded(project).save()
        assert not list(project.glob("*.tmp"))


class TestDeclarations:
    """Primary artifacts: the ones fromwhere did not watch being made."""

    def test_an_attestation_records_who_claimed_it(self, project: Path):
        from fromwhere.declare import declare
        from fromwhere.origins import Author, attested

        artifact = declare(
            project / "data" / "raw.csv", attested(Author("A Researcher"))
        )
        assert artifact.kind == "primary"
        assert artifact.origin_kind == "attested"
        assert [a.name for a in artifact.authors] == ["A Researcher"]

    def test_a_declaration_round_trips(self, project: Path):
        from fromwhere.declare import declare
        from fromwhere.origins import parse_location

        declare(
            project / "data" / "raw.csv",
            parse_location("git", "github.com/u/p/data/raw.csv@a1b2c3d"),
        )
        artifact = Store.load(project).get("data/raw.csv")
        assert artifact.origin_kind == "git"
        assert artifact.origin_revision == "a1b2c3d"


class TestDeclarationSurvivesRewrites:
    """A declaration is the one thing in the record nothing else can rebuild.

    Every writer constructs a fresh `Artifact` describing what it just did, and
    none of them know who declared the file. Merging is done once, in `record`,
    so a writer added later cannot get it wrong.
    """

    def test_authors_survive_a_later_rewrite(self, project: Path):
        from fromwhere.declare import declare
        from fromwhere.origins import Author, attested

        target = project / "data" / "raw.csv"
        declare(
            target, attested(Author("A Researcher"), Author("Claude", "ai"))
        )

        store = Store.load(project)
        store.record(
            Artifact(
                path="data/raw.csv",
                hash=hash_file(target),
                kind="authored",
                inputs=[Input("other.csv", "sha256:aa")],
            )
        )
        store.save()

        artifact = Store.load(project).get("data/raw.csv")
        assert [a.name for a in artifact.authors] == ["A Researcher", "Claude"]
        assert artifact.origin_kind == "attested"
        # ...and the rewrite's own content is kept.
        assert [i.path for i in artifact.inputs] == ["other.csv"]

    def test_an_explicit_new_declaration_wins(self, project: Path):
        from fromwhere.declare import declare
        from fromwhere.origins import Author, attested

        target = project / "data" / "raw.csv"
        declare(target, attested(Author("First")))
        declare(target, attested(Author("Second")))
        assert [
            a.name for a in Store.load(project).get("data/raw.csv").authors
        ] == ["Second"]


class TestTheOldRecordName:
    """`figmint.toml`, from before the tool was renamed.

    Read rather than ignored, because ignoring it looks identical to a project
    with no provenance at all — the one failure mode this file must not have.
    """

    def test_it_is_read_migrated_and_announced(self, project: Path, capsys):
        recorded(project).save()
        legacy = project / LEGACY_STORE_NAME
        (project / STORE_NAME).rename(legacy)

        # Read under the old name, and said out loud.
        store = Store.load(project)
        assert store.get("figures/plot.png").command == "uv run plot.py"
        assert LEGACY_STORE_NAME in capsys.readouterr().err

        # The root is still found by walking up to it.
        assert find_root(project / "figures") == project

        # Recording anything writes the new name, leaving the old file alone
        # rather than deleting a record on the user's behalf.
        store.record(Artifact(path="figures/other.png", hash="sha256:bb"))
        store.save()
        assert (project / STORE_NAME).is_file()
        assert legacy.is_file()
        assert set(Store.load(project).artifacts) == {
            "figures/plot.png",
            "figures/other.png",
        }

    def test_the_new_name_wins_when_both_are_present(self, project: Path):
        recorded(project).save()
        (project / LEGACY_STORE_NAME).write_text(
            '[artifact."figures/stale.png"]\nhash = "sha256:cc"\n'
        )
        assert set(Store.load(project).artifacts) == {"figures/plot.png"}
