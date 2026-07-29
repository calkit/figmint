"""Tests for signing a built figure with C2PA Content Credentials.

These sign real files with a real (locally generated) certificate chain and read
the manifests back, rather than asserting on JSON we constructed ourselves. The
failure mode being guarded against — a signature that looks fine but verifies as
nothing — is invisible to any test that stops short of round-tripping.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from figmint import sign as sign_mod
from figmint.assets import hash_file
from figmint.build import build
from figmint.cli import EXIT_OK, EXIT_STALE, main
from figmint.credentials import read as read_credentials
from figmint.document import load
from figmint.importer import Origin, declare
from figmint.provenance import Level, Policy

EXAMPLES = Path(__file__).parent.parent / "examples"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def identity(tmp_path_factory) -> sign_mod.Identity:
    """A generated local identity, shared across the module (RSA keygen is slow)."""
    return sign_mod.create_local_identity(tmp_path_factory.mktemp("creds"))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copy(EXAMPLES / "two-panel.fig.yaml", tmp_path / "two-panel.fig.yaml")
    shutil.copytree(EXAMPLES / "figures", tmp_path / "figures")
    return tmp_path


class TestLocalIdentity:
    def test_generates_a_usable_chain(self, identity: sign_mod.Identity):
        # c2pa rejects a bare self-signed certificate, so a CA must be present.
        assert identity.cert_chain.count("BEGIN CERTIFICATE") == 2

    def test_the_key_is_not_world_readable(self, tmp_path: Path):
        directory = tmp_path / "creds"
        sign_mod.create_local_identity(directory)
        mode = (directory / "signing-key.pem").stat().st_mode & 0o777
        assert mode == 0o600

    def test_explicit_cert_and_key_win(self, tmp_path: Path, identity):
        cert = tmp_path / "c.pem"
        key = tmp_path / "k.pem"
        cert.write_text(identity.cert_chain)
        key.write_bytes(identity.private_key_pem)
        resolved = sign_mod.resolve_identity(cert, key)
        assert resolved.cert_chain == identity.cert_chain

    def test_cert_without_key_is_rejected(self, tmp_path: Path):
        with pytest.raises(sign_mod.SigningError, match="together"):
            sign_mod.resolve_identity(tmp_path / "c.pem", None)


class TestAlgorithmSelection:
    """The algorithm is a property of the key, not a choice."""

    def test_rsa_keys_use_ps256(self):
        from c2pa import C2paSigningAlg
        from cryptography.hazmat.primitives.asymmetric import rsa

        alg, _ = sign_mod._signer_for(
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
        )
        assert alg == C2paSigningAlg.PS256

    def test_p256_keys_use_es256(self):
        from c2pa import C2paSigningAlg
        from cryptography.hazmat.primitives.asymmetric import ec

        alg, _ = sign_mod._signer_for(ec.generate_private_key(ec.SECP256R1()))
        assert alg == C2paSigningAlg.ES256

    def test_ecdsa_signatures_are_raw_not_der(self):
        # COSE wants fixed-width r||s. `cryptography` returns DER, and signing
        # with the DER form fails only as "COSE signature invalid" — expensive
        # to diagnose, so it is pinned here.
        from cryptography.hazmat.primitives.asymmetric import ec

        _, sign = sign_mod._signer_for(ec.generate_private_key(ec.SECP256R1()))
        signature = sign(b"payload")
        assert len(signature) == 64
        assert not signature.startswith(b"\x30")  # not a DER SEQUENCE

    def test_p384_signatures_are_the_right_width(self):
        from cryptography.hazmat.primitives.asymmetric import ec

        _, sign = sign_mod._signer_for(ec.generate_private_key(ec.SECP384R1()))
        assert len(sign(b"payload")) == 96

    def test_an_unsupported_key_type_is_rejected(self):
        with pytest.raises(sign_mod.SigningError, match="unsupported"):
            sign_mod._signer_for(object())


class TestSourceType:
    def make(self, machine: bool) -> list[sign_mod.Component]:
        return [
            sign_mod.Component(
                key="k",
                path=Path("x.svg"),
                relative="x.svg",
                media_type="image/svg+xml",
                digest="sha256:x",
                machine_generated=machine,
                has_credentials=True,
            )
        ]

    def test_a_plain_composite(self):
        assert sign_mod.digital_source_type(self.make(False)).endswith("/composite")

    def test_one_ai_panel_makes_the_whole_figure_disclose_it(self):
        # The disclosure the standard exists for: a figure containing generated
        # material must say so, not just the panel.
        assert sign_mod.digital_source_type(self.make(True)).endswith(
            "/compositeWithTrainedAlgorithmicMedia"
        )


class TestSigning:
    @pytest.fixture
    def signed(self, project: Path, identity) -> Path:
        document = load(project / "two-panel.fig.yaml")
        results = build(document, formats=("svg",))
        components = sign_mod.collect_components(document)
        sign_mod.sign_file(
            results[0].output, document, components, identity, "0.1.0"
        )
        return results[0].output

    def test_the_signature_verifies(self, signed: Path):
        creds = read_credentials(signed)
        assert creds is not None
        # `Valid` rather than `Trusted` because a locally generated CA is not in
        # any trust list, which is expected — but the maths must check out.
        assert creds.validationState == "Valid"

    def test_figmint_is_recorded_as_the_generator(self, signed: Path):
        creds = read_credentials(signed)
        assert creds is not None
        assert creds.claimGenerator is not None
        assert "figmint" in creds.claimGenerator

    def test_every_panel_becomes_a_component_ingredient(self, signed: Path):
        creds = read_credentials(signed)
        assert creds is not None
        assert len(creds.ingredients) == 2
        assert all(i.relationship == "componentOf" for i in creds.ingredients)
        titles = {i.title for i in creds.ingredients}
        assert titles == {"cp_curve.svg", "wake_profile.svg"}

    def test_a_digital_source_type_is_always_set(self, signed: Path):
        # C2PA v2 requires it on `c2pa.created`; omitting it is what makes
        # Stencila's own signed output validate as Invalid.
        creds = read_credentials(signed)
        assert creds is not None
        assert creds.digitalSourceType == "composite"

    def test_the_composition_assertion_records_input_digests(
        self, project: Path, signed: Path
    ):
        # This is what a future verifier needs to recompute the build and
        # compare — the basis for "the output reflects the inputs as described".
        import json

        from c2pa import Reader

        with Reader(str(signed)) as reader:
            store = json.loads(reader.json())
        manifest = store["manifests"][store["active_manifest"]]
        composition = next(
            a["data"]
            for a in manifest["assertions"]
            if a["label"] == sign_mod.COMPOSITION_ASSERTION
        )
        assert composition["figureId"] == "fig-turbine-performance"
        assert composition["canvas"] == {"width": 468, "height": 210, "units": "pt"}

        digests = {c["path"]: c["digest"] for c in composition["components"]}
        assert digests["figures/cp_curve.svg"] == hash_file(
            project / "figures" / "cp_curve.svg"
        )

    def test_signing_is_in_place(self, signed: Path):
        assert signed.exists()
        assert not signed.with_suffix(signed.suffix + ".signed").exists()

    def test_tampering_after_signing_is_detected(self, signed: Path):
        signed.write_bytes(signed.read_bytes() + b"<!-- tampered -->")
        creds = read_credentials(signed)
        # Either the manifest no longer parses or it reports as invalid; both
        # are correct, silently passing would not be.
        assert creds is None or creds.validationState == "Invalid"


class TestPolicyGate:
    def test_an_anonymous_component_blocks_signing(self, tmp_path: Path):
        (tmp_path / "figures").mkdir()
        (tmp_path / "figures" / "anon.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
        )
        (tmp_path / "f.fig.yaml").write_text(
            "figmint: '0.1'\n"
            "id: t\n"
            "canvas: {width: 100, height: 100, units: pt}\n"
            "sources: {anon: {path: figures/anon.svg}}\n"
            "nodes: [{id: p, type: image, source: anon, "
            "x: 0, y: 0, width: 10, height: 10}]\n"
        )
        document = load(tmp_path / "f.fig.yaml")
        problems = sign_mod.policy_violations(document, Policy(require=Level.DECLARED))
        assert len(problems) == 1
        assert "unidentified" in problems[0]

    def test_declaring_the_origin_clears_the_block(self, tmp_path: Path):
        (tmp_path / "figures").mkdir()
        target = tmp_path / "figures" / "anon.svg"
        target.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
        declare(target, Origin(doi="10.1000/x"))
        (tmp_path / "f.fig.yaml").write_text(
            "figmint: '0.1'\n"
            "id: t\n"
            "canvas: {width: 100, height: 100, units: pt}\n"
            "sources: {anon: {path: figures/anon.svg}}\n"
            "nodes: [{id: p, type: image, source: anon, "
            "x: 0, y: 0, width: 10, height: 10}]\n"
        )
        document = load(tmp_path / "f.fig.yaml")
        assert sign_mod.policy_violations(document, Policy(require=Level.DECLARED)) == []

    def test_a_recorded_origin_in_the_document_counts(self, project: Path):
        # The example records `generatedBy` on its sources, which is provenance
        # even though the files carry no sidecar of their own.
        document = load(project / "two-panel.fig.yaml")
        assert sign_mod.policy_violations(document, Policy(require=Level.DECLARED)) == []


class TestSignCli:
    def test_build_sign_produces_a_signed_artifact(self, project: Path, capsys):
        assert main(["build", str(project), "--sign"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "signed" in out
        assert read_credentials(project / "two-panel.svg") is not None

    def test_build_without_sign_leaves_the_artifact_unsigned(self, project: Path):
        main(["build", str(project)])
        assert read_credentials(project / "two-panel.svg") is None

    def test_refusing_to_sign_exits_non_zero(self, tmp_path: Path, capsys):
        (tmp_path / "figures").mkdir()
        (tmp_path / "figures" / "anon.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
        )
        (tmp_path / "f.fig.yaml").write_text(
            "figmint: '0.1'\n"
            "id: t\n"
            "canvas: {width: 100, height: 100, units: pt}\n"
            "sources: {anon: {path: figures/anon.svg}}\n"
            "nodes: [{id: p, type: image, source: anon, "
            "x: 0, y: 0, width: 10, height: 10}]\n"
        )
        assert main(["build", str(tmp_path), "--sign"]) == EXIT_STALE
        assert "refusing to sign" in capsys.readouterr().err

    def test_a_refused_figure_is_still_built_but_left_unsigned(
        self, tmp_path: Path
    ):
        (tmp_path / "figures").mkdir()
        (tmp_path / "figures" / "anon.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
        )
        (tmp_path / "f.fig.yaml").write_text(
            "figmint: '0.1'\n"
            "id: t\n"
            "canvas: {width: 100, height: 100, units: pt}\n"
            "sources: {anon: {path: figures/anon.svg}}\n"
            "nodes: [{id: p, type: image, source: anon, "
            "x: 0, y: 0, width: 10, height: 10}]\n"
        )
        main(["build", str(tmp_path), "--sign"])
        # You still get your figure — you just do not get a signature vouching
        # for something figmint cannot vouch for.
        assert (tmp_path / "f.svg").exists()
        assert read_credentials(tmp_path / "f.svg") is None

    def test_advisory_policy_signs_with_a_warning(self, tmp_path: Path, capsys):
        (tmp_path / "figmint.toml").write_text("[provenance]\nenforce = false\n")
        (tmp_path / "figures").mkdir()
        (tmp_path / "figures" / "anon.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
        )
        (tmp_path / "f.fig.yaml").write_text(
            "figmint: '0.1'\n"
            "id: t\n"
            "canvas: {width: 100, height: 100, units: pt}\n"
            "sources: {anon: {path: figures/anon.svg}}\n"
            "nodes: [{id: p, type: image, source: anon, "
            "x: 0, y: 0, width: 10, height: 10}]\n"
        )
        assert main(["build", str(tmp_path), "--sign"]) == EXIT_OK
        assert "signing anyway" in capsys.readouterr().err
