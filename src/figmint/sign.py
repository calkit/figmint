"""Signing a built figure with C2PA Content Credentials.

figmint has read credentials from its components since early on. This is the
other half: stating, in a signed manifest, what the composite is made of.

What the signature asserts
--------------------------
Each panel becomes a `componentOf` ingredient — C2PA's own vocabulary for "this
asset was composited from those". Where a panel carries its own manifest, that
manifest is pulled in with it, so the provenance chain nests rather than
flattening into a claim about the top-level file only.

The creation action carries a `digitalSourceType`, which is required by the C2PA
v2 spec (Stencila's `credentials sign` omits it, and its output is rejected as a
result). A figmint figure is `composite`, or
`compositeWithTrainedAlgorithmicMedia` when any panel declares generative-AI
origin — so "does this figure contain AI-generated material?" is answered by the
signature rather than by a convention someone has to remember to follow.

Signing refuses when a component fails the project's provenance policy. A
signature over a figure containing an anonymous panel would assert far less than
it appears to, and the whole point of the policy is that such a figure should
not be published in the first place.

Identity
--------
A local self-signed identity, created on first use. The longer-term intent is a
cloud certificate attesting that *the output truly reflects the inputs as
described* — a claim about build integrity rather than authorship. That is why
identity resolution is a list of sources rather than a hard-coded path, and why
the composition assertion records input digests: a future verifier needs to be
able to recompute the build and compare.
"""

from __future__ import annotations

import datetime as _datetime
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import credentials as credentials_mod
from . import provenance as provenance_mod
from .assets import MEDIA_TYPES, hash_file
from .document import Document

logger = logging.getLogger(__name__)

IPTC = "http://cv.iptc.org/newscodes/digitalsourcetype"

#: Assertion label for figmint's own composition record.
COMPOSITION_ASSERTION = "org.figmint.composition"

#: Where a generated local identity lives.
CONFIG_DIR = Path(
    os.environ.get("FIGMINT_CONFIG_DIR")
    or (Path.home() / "Library" / "Application Support" / "io.figmint")
    if os.uname().sysname == "Darwin"
    else Path.home() / ".config" / "figmint"
)


class SigningError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@dataclass
class Identity:
    """A certificate chain and key figmint can sign with."""

    cert_chain: str
    private_key_pem: bytes
    #: Where this came from, so the CLI can say what it used.
    source: str
    #: RFC 3161 timestamp authority. None signs offline, which keeps `build`
    #: usable without network at the cost of a timestamp.
    tsa_url: str | None = None

    @property
    def common_name(self) -> str:
        try:
            from cryptography import x509

            cert = x509.load_pem_x509_certificate(self.cert_chain.encode())
            return cert.subject.rfc4514_string()
        except Exception:  # noqa: BLE001 - display only
            return "unknown"


def _stencila_identity() -> Identity | None:
    """Reuse Stencila's local signing identity when it exists.

    Projects that already run `stencila credentials init` should not need a
    second identity for the same purpose on the same machine.
    """
    base = (
        Path.home()
        / "Library"
        / "Application Support"
        / "io.stencila.stencila"
        / "credentials"
    )
    cert = base / "local-signing-cert.pem"
    key = base / "local-signing-key.pem"
    if cert.is_file() and key.is_file():
        return Identity(
            cert_chain=cert.read_text(),
            private_key_pem=key.read_bytes(),
            source=f"Stencila local identity ({cert})",
        )
    return None


def create_local_identity(directory: Path | None = None) -> Identity:
    """Generate a self-signed CA and a leaf certificate to sign with.

    A bare self-signed certificate is rejected by c2pa — it requires a chain, and
    the leaf needs `emailProtection` extended key usage. So this mints a tiny
    two-certificate chain rather than a single cert.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    directory = directory or CONFIG_DIR / "credentials"
    directory.mkdir(parents=True, exist_ok=True)

    now = _datetime.datetime.now(_datetime.timezone.utc)
    expires = now + _datetime.timedelta(days=3650)

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "figmint local CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "figmint"),
        ]
    )
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(expires)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        # c2pa-rs rejects a chain without key identifiers ("the certificate is
        # invalid", with no further detail). openssl adds these by default,
        # which is why a hand-rolled chain works and an obvious Python
        # translation of it does not.
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(
                        NameOID.COMMON_NAME, "figmint local signing identity"
                    ),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "figmint"),
                ]
            )
        )
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(expires)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        # c2pa requires this EKU on the signing certificate.
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    chain = (
        leaf_cert.public_bytes(serialization.Encoding.PEM)
        + ca_cert.public_bytes(serialization.Encoding.PEM)
    ).decode()
    key_pem = leaf_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    (directory / "signing-cert.pem").write_text(chain)
    key_path = directory / "signing-key.pem"
    key_path.write_bytes(key_pem)
    key_path.chmod(0o600)

    return Identity(
        cert_chain=chain,
        private_key_pem=key_pem,
        source=f"figmint local identity ({directory})",
    )


def resolve_identity(
    cert: Path | None = None,
    key: Path | None = None,
    tsa_url: str | None = None,
    *,
    create: bool = True,
) -> Identity:
    """Find a signing identity, creating a local one if nothing else is set up.

    Ordered so that an explicit choice always wins and the convenient default is
    last: flags, then environment, then a previously-generated local identity,
    then Stencila's, then a fresh one.
    """
    tsa = tsa_url or os.environ.get("FIGMINT_TSA_URL") or None

    if cert or key:
        if not (cert and key):
            raise SigningError("--cert and --key must be given together")
        return Identity(
            cert_chain=cert.read_text(),
            private_key_pem=key.read_bytes(),
            source=str(cert),
            tsa_url=tsa,
        )

    env_cert = os.environ.get("FIGMINT_SIGNING_CERT")
    env_key = os.environ.get("FIGMINT_SIGNING_KEY")
    if env_cert and env_key:
        return Identity(
            cert_chain=Path(env_cert).read_text(),
            private_key_pem=Path(env_key).read_bytes(),
            source="FIGMINT_SIGNING_CERT",
            tsa_url=tsa,
        )

    local_dir = CONFIG_DIR / "credentials"
    local_cert = local_dir / "signing-cert.pem"
    local_key = local_dir / "signing-key.pem"
    if local_cert.is_file() and local_key.is_file():
        return Identity(
            cert_chain=local_cert.read_text(),
            private_key_pem=local_key.read_bytes(),
            source=f"figmint local identity ({local_dir})",
            tsa_url=tsa,
        )

    stencila = _stencila_identity()
    if stencila:
        stencila.tsa_url = tsa
        return stencila

    if not create:
        raise SigningError("no signing identity available")
    identity = create_local_identity(local_dir)
    identity.tsa_url = tsa
    return identity


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass
class Component:
    """One panel, as it will be recorded in the manifest."""

    key: str
    path: Path
    relative: str
    media_type: str
    digest: str
    machine_generated: bool
    has_credentials: bool


def collect_components(document: Document) -> list[Component]:
    """Gather the components a document actually places, in document order."""
    out: list[Component] = []
    for key in document.used_source_keys():
        source = document.sources.get(key) or {}
        path = document.source_path(key)
        if path is None or not path.exists():
            continue
        creds = credentials_mod.read(path)
        out.append(
            Component(
                key=key,
                path=path,
                relative=str(source.get("path") or key),
                media_type=MEDIA_TYPES.get(
                    path.suffix.lower(), "application/octet-stream"
                ),
                digest=hash_file(path),
                machine_generated=bool(creds and creds.machineGenerated),
                has_credentials=creds is not None,
            )
        )
    return out


def digital_source_type(components: list[Component]) -> str:
    """The IPTC source type for the composite.

    A figure assembled from several sources is `composite`. If any panel was
    generated by AI, the standard has a specific value for exactly that, and
    using it is the difference between disclosure and burying the fact.
    """
    if any(c.machine_generated for c in components):
        return f"{IPTC}/compositeWithTrainedAlgorithmicMedia"
    return f"{IPTC}/composite"


def build_manifest(
    document: Document,
    components: list[Component],
    output_format: str,
    version: str,
) -> dict[str, Any]:
    """The manifest JSON describing this composite."""
    canvas = document.canvas
    return {
        "claim_generator_info": [{"name": "figmint", "version": version}],
        "title": document.title or document.id,
        "format": output_format,
        "assertions": [
            {
                "label": "c2pa.actions.v2",
                "data": {
                    "actions": [
                        {
                            "action": "c2pa.created",
                            "softwareAgent": {"name": "figmint", "version": version},
                            "digitalSourceType": digital_source_type(components),
                            "description": "Compose figure from components",
                        }
                    ]
                },
            },
            {
                # figmint's own record, analogous to `org.stencila.provenance`:
                # enough to recompute the build and compare against this claim.
                "label": COMPOSITION_ASSERTION,
                "data": {
                    "figureId": document.id,
                    "canvas": {
                        "width": canvas.get("width"),
                        "height": canvas.get("height"),
                        "units": canvas.get("units", "pt"),
                    },
                    "components": [
                        {
                            "key": c.key,
                            "path": c.relative,
                            "digest": c.digest,
                            "mediaType": c.media_type,
                            "hasCredentials": c.has_credentials,
                        }
                        for c in components
                    ],
                },
            },
        ],
    }


def ingredient_json(component: Component) -> dict[str, Any]:
    """Ingredient record for one panel.

    `componentOf` is C2PA's term for "composited into" — the relationship a
    figmint panel actually has to the figure, as opposed to `parentOf` (edited
    from) or `inputTo` (fed to a model).
    """
    return {
        "title": Path(component.relative).name,
        "format": component.media_type,
        "relationship": "componentOf",
    }


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def _signer_for(private_key: Any):
    """Pick the signing algorithm from the key, and return a matching callback.

    The algorithm is a property of the key, not a choice: signing an ECDSA key
    with RSA-PSS padding produces bytes that verify as nothing, and c2pa reports
    it only as "COSE signature invalid" — so getting this wrong is expensive to
    diagnose. Supporting whatever key a user brings (or another tool generated)
    means reading it off the key itself.
    """
    from c2pa import C2paSigningAlg
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    if isinstance(private_key, rsa.RSAPrivateKey):

        def sign_rsa(data: bytes) -> bytes:
            return private_key.sign(
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=hashes.SHA256.digest_size,
                ),
                hashes.SHA256(),
            )

        return C2paSigningAlg.PS256, sign_rsa

    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        curve = private_key.curve.name
        if curve == "secp256r1":
            digest, alg, size = hashes.SHA256(), C2paSigningAlg.ES256, 32
        elif curve == "secp384r1":
            digest, alg, size = hashes.SHA384(), C2paSigningAlg.ES384, 48
        elif curve == "secp521r1":
            digest, alg, size = hashes.SHA512(), C2paSigningAlg.ES512, 66
        else:
            raise SigningError(f"unsupported elliptic curve for signing: {curve}")

        def sign_ec(data: bytes) -> bytes:
            # `cryptography` returns a DER-encoded (r, s); COSE wants the raw
            # fixed-width concatenation, so unpack and re-pad.
            der = private_key.sign(data, ec.ECDSA(digest))
            r, s = decode_dss_signature(der)
            return r.to_bytes(size, "big") + s.to_bytes(size, "big")

        return alg, sign_ec

    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        return C2paSigningAlg.ED25519, private_key.sign

    raise SigningError(
        f"unsupported signing key type: {type(private_key).__name__}"
    )


@dataclass
class SignResult:
    output: Path
    identity: str
    components: int
    machine_generated: bool
    warnings: list[str] = field(default_factory=list)


def policy_violations(
    document: Document,
    policy: provenance_mod.Policy,
    project: Any = None,
) -> list[str]:
    """Components that fall below the project's provenance bar.

    Checked against what is on disk right now, not against what the document
    recorded when the panel was placed — a signature should describe the file
    being signed, not a historical claim about it.
    """
    problems: list[str] = []
    for key in document.used_source_keys():
        source = document.sources.get(key) or {}
        path = document.source_path(key)
        if path is None or not path.exists():
            problems.append(f"{key}: file not found")
            continue
        creds = credentials_mod.read(path)
        stage = project.stage_for(path) if project else None
        from .assets import merge_provenance

        recorded = merge_provenance(path, path.parent, creds)
        # Anything the document recorded still counts — an origin declared in a
        # sidecar or written at import time is provenance too.
        recorded = {**(source.get("provenance") or {}), **recorded}
        assessment = provenance_mod.assess(
            recorded, creds.to_dict() if creds else None, stage
        )
        if not policy.permits(assessment.level):
            problems.append(
                f"{key} ({source.get('path', key)}): {assessment.level.slug} — "
                f"{assessment.reason}"
            )
    return problems


def sign_file(
    target: Path,
    document: Document,
    components: list[Component],
    identity: Identity,
    version: str,
) -> SignResult:
    """Embed a signed manifest in a built artifact, in place."""
    from c2pa import Builder, Context, Signer, C2paSigningAlg
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    media_type = MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
    manifest = build_manifest(document, components, media_type, version)

    private_key = serialization.load_pem_private_key(
        identity.private_key_pem, password=None
    )
    algorithm, sign_bytes = _signer_for(private_key)

    warnings: list[str] = []
    signed = target.with_suffix(target.suffix + ".signed")

    try:
        with Context() as ctx:
            with Signer.from_callback(
                sign_bytes,
                algorithm,
                identity.cert_chain,
                identity.tsa_url,
            ) as signer:
                with Builder(json.dumps(manifest), ctx) as builder:
                    for component in components:
                        try:
                            with component.path.open("rb") as handle:
                                builder.add_ingredient(
                                    ingredient_json(component),
                                    component.media_type,
                                    handle,
                                )
                        except Exception as exc:  # noqa: BLE001
                            # A panel we cannot record is worth saying out loud;
                            # the manifest would otherwise silently understate
                            # what the figure is made of.
                            warnings.append(
                                f"could not record {component.relative} as an "
                                f"ingredient: {exc}"
                            )
                    builder.sign_file(str(target), str(signed), signer)
    except Exception as exc:  # noqa: BLE001 - surfaced as a CLI error
        signed.unlink(missing_ok=True)
        raise SigningError(f"signing failed: {exc}") from exc

    signed.replace(target)
    return SignResult(
        output=target,
        identity=identity.source,
        components=len(components),
        machine_generated=any(c.machine_generated for c in components),
        warnings=warnings,
    )
