"""Signing, and the ordering it forces.

Embedding a manifest rewrites the file. That single fact drives the design of
`fromwhere run`: the artifact has to be signed *before* it is hashed, or every
signed artifact reads as modified the moment it is produced. The test for that
is the one worth having.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fromwhere import credentials
from fromwhere.run import run
from fromwhere.sign import (
    Ingredient,
    _default_config_dir,
    build_manifest,
    collect_ingredients,
    digital_source_type,
    sign_artifact,
)
from fromwhere.status import State, check_path
from fromwhere.store import Input, Store, hash_file


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    # Keep the generated identity out of the developer's real config.
    monkeypatch.setenv("FROMWHERE_CONFIG_DIR", str(tmp_path / ".fromwhere"))
    import importlib

    import fromwhere.sign as sign_mod

    importlib.reload(sign_mod)

    (tmp_path / ".git").mkdir()
    (tmp_path / "uv.lock").write_text("lock")
    (tmp_path / "data.csv").write_text("x,y\n1,2\n")
    return tmp_path


def _png(path: Path) -> Path:
    """A real PNG — c2pa parses the format, so a stub will not do."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(figsize=(2, 1))
    axes.plot([1, 2, 3], [1, 4, 9])
    figure.savefig(path)
    plt.close(figure)
    return path


class TestConfigDir:
    def test_it_resolves_on_every_platform(self, monkeypatch, tmp_path):
        # `os.uname` does not exist on Windows, so this is read from
        # `sys.platform` — getting it wrong is an import-time crash, not a
        # wrong path.
        monkeypatch.setattr(sys, "platform", "darwin")
        assert _default_config_dir().parts[-3:] == (
            "Library",
            "Application Support",
            "io.fromwhere",
        )
        monkeypatch.setattr(sys, "platform", "linux")
        assert _default_config_dir().parts[-2:] == (".config", "fromwhere")
        # Windows keeps the private key out of the roaming profile.
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
        assert (
            _default_config_dir()
            == tmp_path / "AppData" / "Local" / "fromwhere"
        )
        monkeypatch.delenv("LOCALAPPDATA")
        assert _default_config_dir() == Path.home() / "fromwhere"


class TestManifest:
    def test_a_plain_artifact_is_composite(self):
        assert digital_source_type([]).endswith("/composite")

    def test_an_ai_input_is_disclosed(self):
        """The IPTC vocabulary has a value for exactly this, and using it is
        the difference between disclosure and burying the fact."""
        ingredient = Ingredient(
            path=Path("x.png"),
            relative="x.png",
            kind="file",
            digest="sha256:aa",
            media_type="image/png",
            machine_generated=True,
            has_credentials=True,
        )
        assert digital_source_type([ingredient]).endswith(
            "/compositeWithTrainedAlgorithmicMedia"
        )

    def test_the_manifest_records_input_digests(self, project: Path):
        """So a verifier can check the artifact without the repository the
        record lives in."""
        manifest = build_manifest(
            project / "out.png",
            collect_ingredients(
                [Input("data.csv", hash_file(project / "data.csv"))], project
            ),
            "uv run plot.py",
            "0.1.0",
        )
        composition = next(
            a
            for a in manifest["assertions"]
            if a["label"] == "org.fromwhere.composition"
        )
        assert composition["data"]["inputs"][0]["path"] == "data.csv"
        assert composition["data"]["command"] == "uv run plot.py"

    def test_a_missing_input_is_skipped_not_fatal(self, project: Path):
        """Describing most of an artifact's origin beats describing none of it;
        a missing input is `fromwhere status`'s complaint, not signing's."""
        found = collect_ingredients([Input("gone.csv", "sha256:aa")], project)
        assert found == []


class TestSigning:
    def test_a_signed_artifact_verifies(self, project: Path):
        target = _png(project / "plot.png")
        result = sign_artifact(
            target,
            [Input("data.csv", hash_file(project / "data.csv"))],
            project,
        )
        assert result.ingredients == 1
        parsed = credentials.read(target)
        assert parsed is not None
        assert parsed.validationState in ("Valid", "Trusted")
        assert parsed.claimGenerator.startswith("fromwhere")

    def test_signing_changes_the_bytes(self, project: Path):
        """The fact that forces the ordering in `fromwhere run`."""
        target = _png(project / "plot.png")
        before = hash_file(target)
        sign_artifact(target, [], project)
        assert hash_file(target) != before

    def test_a_signed_artifact_is_not_immediately_stale(self, project: Path):
        """The regression this ordering exists to prevent: hashing before
        signing would make every signed artifact read as edited the moment it
        was written."""
        (project / "make.py").write_text(
            "import matplotlib; matplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "f, a = plt.subplots(figsize=(2,1)); a.plot([1,2],[1,2])\n"
            "f.savefig('plot.png')\n"
        )
        run(
            ["uv", "run", "--no-project", sys.executable, "make.py"],
            inputs=[project / "data.csv"],
            outputs=[project / "plot.png"],
            cwd=project,
        )
        assert Store.load(project).get("plot.png").signed
        assert check_path(project / "plot.png").state is State.OK

    def test_a_format_that_cannot_carry_a_manifest_is_still_recorded(
        self, project: Path
    ):
        """Refusing to produce a .drawio because it cannot be signed would be
        absurd; it has provenance worth recording either way."""
        (project / "make.py").write_text(
            "open('out.drawio','w').write('<mxfile/>')"
        )
        result = run(
            ["uv", "run", "--no-project", sys.executable, "make.py"],
            inputs=[],
            outputs=[project / "out.drawio"],
            cwd=project,
        )
        assert result.artifacts[0].signed is False
        assert check_path(project / "out.drawio").state is State.OK

    def test_signing_can_be_turned_off(self, project: Path):
        (project / "make.py").write_text(
            "import matplotlib; matplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "f, a = plt.subplots(figsize=(2,1)); f.savefig('plot.png')\n"
        )
        result = run(
            ["uv", "run", "--no-project", sys.executable, "make.py"],
            inputs=[],
            outputs=[project / "plot.png"],
            cwd=project,
            sign=False,
        )
        assert result.artifacts[0].signed is False
        assert credentials.read(project / "plot.png") is None


class TestFormatSupport:
    """Which formats can actually carry a manifest.

    Verified against the library rather than assumed, because the failure is
    invisible until signing time: a format c2pa cannot embed into raises
    `NotSupported`, and a `fromwhere run` that produced a PDF would die at the
    last step of an otherwise successful build.
    """

    @pytest.mark.parametrize("suffix", [".pdf", ".html", ".mp4", ".heic"])
    def test_unsignable_formats_are_not_claimed(self, suffix: str):
        from fromwhere.credentials import SIGNABLE_SUFFIXES

        assert suffix not in SIGNABLE_SUFFIXES

    @pytest.mark.parametrize("suffix", [".png", ".jpg", ".svg", ".webp"])
    def test_signable_formats_are_claimed(self, suffix: str):
        from fromwhere.credentials import SIGNABLE_SUFFIXES

        assert suffix in SIGNABLE_SUFFIXES

    def test_every_claimed_format_really_signs(self, tmp_path: Path):
        """The list is only worth having if the library agrees with it."""
        from fromwhere.credentials import SIGNABLE_SUFFIXES
        from fromwhere.sign import SigningError, sign_artifact

        for suffix in sorted(SIGNABLE_SUFFIXES):
            target = tmp_path / f"probe{suffix}"
            target.write_bytes(b"\x00" * 64)  # invalid content on purpose
            try:
                sign_artifact(target, [], tmp_path, command="probe")
            except SigningError as exc:
                # Rejecting the *content* is expected; rejecting the *type* is
                # the claim being tested.
                assert "type is unsupported" not in str(exc), suffix

    def test_a_pdf_output_does_not_break_a_run(self, tmp_path: Path):
        """`calkit latex build` produces a PDF, and that has to keep working
        even though the PDF cannot carry credentials."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "uv.lock").write_text("lock")
        (tmp_path / "make_pdf.py").write_text(
            "open('paper.pdf','wb').write(b'%PDF-1.4')\n"
        )
        from fromwhere.run import run

        result = run(
            ["uv", "run", "python", "make_pdf.py"],
            inputs=[],
            outputs=[tmp_path / "paper.pdf"],
            cwd=tmp_path,
        )
        assert (tmp_path / "paper.pdf").is_file()
        assert result.artifacts[0].signed is False


class TestReadableButNotSignable:
    """PDF is the asymmetric case, and the asymmetry is the point.

    c2pa-rs recognizes a PDF well enough to look for a manifest in one, but
    cannot embed a manifest into it. So fromwhere keeps two lists: what it can
    read credentials from, and the strictly smaller set it can write them to.
    """

    def test_pdf_can_be_read_from_but_not_signed(self):
        from fromwhere.credentials import (
            CREDENTIALED_SUFFIXES,
            SIGNABLE_SUFFIXES,
        )

        assert ".pdf" in CREDENTIALED_SUFFIXES
        assert ".pdf" not in SIGNABLE_SUFFIXES

    def test_everything_signable_is_also_readable(self):
        from fromwhere.credentials import (
            CREDENTIALED_SUFFIXES,
            SIGNABLE_SUFFIXES,
        )

        assert SIGNABLE_SUFFIXES <= CREDENTIALED_SUFFIXES

    def test_the_reader_really_accepts_a_pdf(self, tmp_path: Path):
        """Verified against the library, since the whole split rests on it."""
        from c2pa import Reader

        pdf = tmp_path / "t.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        try:
            with pdf.open("rb") as handle:
                Reader("application/pdf", handle)
        except Exception as exc:  # noqa: BLE001
            # "no manifest here" is fine; "I don't know this type" is not.
            assert "unsupported" not in str(exc).lower()
