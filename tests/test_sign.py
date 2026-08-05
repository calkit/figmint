"""Signing, and the ordering it forces.

Embedding a manifest rewrites the file. That single fact drives the design of
`figmint run`: the artifact has to be signed *before* it is hashed, or every
signed artifact reads as modified the moment it is produced. The test for that
is the one worth having.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from figmint import credentials
from figmint.run import run
from figmint.sign import (
    Ingredient,
    build_manifest,
    collect_ingredients,
    digital_source_type,
    sign_artifact,
)
from figmint.status import State, check_path
from figmint.store import Input, Store, hash_file


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    # Keep the generated identity out of the developer's real config.
    monkeypatch.setenv("FIGMINT_CONFIG_DIR", str(tmp_path / ".figmint"))
    import importlib

    import figmint.sign as sign_mod

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
            if a["label"] == "org.figmint.composition"
        )
        assert composition["data"]["inputs"][0]["path"] == "data.csv"
        assert composition["data"]["command"] == "uv run plot.py"

    def test_a_missing_input_is_skipped_not_fatal(self, project: Path):
        """Describing most of an artifact's origin beats describing none of it;
        a missing input is `figmint status`'s complaint, not signing's."""
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
        assert parsed.claimGenerator.startswith("figmint")

    def test_signing_changes_the_bytes(self, project: Path):
        """The fact that forces the ordering in `figmint run`."""
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
