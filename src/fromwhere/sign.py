"""Signing an artifact with C2PA Content Credentials.

`provenance.toml` records what an artifact came from; a signature says the same
thing in a form that travels with the file. Once a figure leaves the repository
— pasted into a manuscript, mailed to a co-author — the record is behind and the
manifest is all that is left.

What the signature asserts
--------------------------
Every recorded input becomes a `componentOf` ingredient — C2PA's own vocabulary
for "this asset was composited from those" — carrying the input's own manifest
where it has one, so the chain nests rather than flattening into a claim about
the top-level file.

The creation action carries a `digitalSourceType`, which the C2PA v2 spec
requires. An artifact is `composite`, or `compositeWithTrainedAlgorithmicMedia`
when any input declares generative-AI origin. That makes "does this figure
contain AI-generated material?" a question answered by the signature rather than
by a convention someone has to remember to follow.

Alongside those, fromwhere writes its own assertion holding the input digests,
so a verifier can recompute what `fromwhere status` checks without the
repository.

Identity
--------
A local self-signed identity, created on first use, or a certificate given with
`--cert`. The longer-term intent is a cloud certificate attesting that *the
output truly reflects the inputs as described* — build integrity rather than
authorship — which is why identity resolution is a list of sources rather than a
path, and why the assertion records digests at all.
"""

from __future__ import annotations

import datetime as _datetime
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import credentials as credentials_mod
from .store import Input

logger = logging.getLogger(__name__)

IPTC = "http://cv.iptc.org/newscodes/digitalsourcetype"

#: Assertion label for fromwhere's own record of what went in.
COMPOSITION_ASSERTION = "org.fromwhere.composition"

#: Media types fromwhere needs to name. Not exhaustive — anything unlisted is
#: signed as a byte stream, which c2pa handles.
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".xcf": "image/x-xcf",
    # Deliberately not `application/xml`: c2pa refuses to encode an ingredient
    # declared as XML ("unable to encode assertion data") and the failure only
    # surfaces at sign time, after the ingredient was accepted. A diagram is
    # recorded as an opaque blob, which is all an ingredient needs to be —
    # the hash is what carries the meaning.
    ".drawio": "application/octet-stream",
    ".toml": "text/plain",
    ".lock": "text/plain",
    ".yaml": "text/plain",
    ".yml": "text/plain",
}


def _default_config_dir() -> Path:
    """Where a generated local identity lives, per platform.

    `os.uname` does not exist on Windows, so the platform is read from
    `sys.platform`. Windows gets `%LOCALAPPDATA%` rather than the roaming
    `%APPDATA%`: what lands here is a private key, which should not follow an
    account between machines.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "io.fromwhere"
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) if local else Path.home()) / "fromwhere"
    return Path.home() / ".config" / "fromwhere"


#: Where a generated local identity lives.
CONFIG_DIR = Path(
    os.environ.get("FROMWHERE_CONFIG_DIR") or _default_config_dir()
)


def media_type(path: Path) -> str:
    return MEDIA_TYPES.get(
        Path(path).suffix.lower(), "application/octet-stream"
    )


class SigningError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@dataclass
class Identity:
    """A certificate chain and key fromwhere can sign with."""

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
            x509.NameAttribute(NameOID.COMMON_NAME, "fromwhere local CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "fromwhere"),
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
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0), critical=True
        )
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
                        NameOID.COMMON_NAME, "fromwhere local signing identity"
                    ),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "fromwhere"),
                ]
            )
        )
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(expires)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
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
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()
            ),
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
        source=f"fromwhere local identity ({directory})",
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
    last: flags, then environment, then a previously-generated
    local identity, then a fresh one.
    """
    tsa = tsa_url or os.environ.get("FROMWHERE_TSA_URL") or None

    if cert or key:
        if not (cert and key):
            raise SigningError("--cert and --key must be given together")
        return Identity(
            cert_chain=cert.read_text(),
            private_key_pem=key.read_bytes(),
            source=str(cert),
            tsa_url=tsa,
        )

    env_cert = os.environ.get("FROMWHERE_SIGNING_CERT")
    env_key = os.environ.get("FROMWHERE_SIGNING_KEY")
    if env_cert and env_key:
        return Identity(
            cert_chain=Path(env_cert).read_text(),
            private_key_pem=Path(env_key).read_bytes(),
            source="FROMWHERE_SIGNING_CERT",
            tsa_url=tsa,
        )

    local_dir = CONFIG_DIR / "credentials"
    local_cert = local_dir / "signing-cert.pem"
    local_key = local_dir / "signing-key.pem"
    if local_cert.is_file() and local_key.is_file():
        return Identity(
            cert_chain=local_cert.read_text(),
            private_key_pem=local_key.read_bytes(),
            source=f"fromwhere local identity ({local_dir})",
            tsa_url=tsa,
        )

    if not create:
        raise SigningError("no signing identity available")
    identity = create_local_identity(local_dir)
    identity.tsa_url = tsa
    return identity


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass
class Ingredient:
    """One recorded input, as it will appear in the manifest."""

    path: Path
    relative: str
    kind: str
    digest: str
    media_type: str
    machine_generated: bool
    has_credentials: bool


def collect_ingredients(inputs: list[Input], root: Path) -> list[Ingredient]:
    """Resolve recorded inputs against the filesystem, skipping what is gone.

    A missing input is dropped rather than raising: the artifact still exists
    and describing most of its origin beats describing none of it. `fromwhere
    status` is the check that complains about a missing input, and it is a
    freshness question rather than a signing one.
    """
    out: list[Ingredient] = []
    for item in inputs:
        path = (root / item.path).resolve()
        if not path.is_file():
            logger.warning(
                "input %s not found; omitted from the manifest", item.path
            )
            continue
        creds = credentials_mod.read(path)
        out.append(
            Ingredient(
                path=path,
                relative=item.path,
                kind=item.kind,
                digest=item.hash,
                media_type=media_type(path),
                machine_generated=bool(creds and creds.machineGenerated),
                has_credentials=creds is not None,
            )
        )
    return out


def digital_source_type(ingredients: list[Ingredient]) -> str:
    """The IPTC source type for the artifact.

    Anything assembled from inputs is `composite`. If any input was generated by
    AI, the standard has a value for exactly that, and using it is the
    difference between disclosure and burying the fact.
    """
    if any(i.machine_generated for i in ingredients):
        return f"{IPTC}/compositeWithTrainedAlgorithmicMedia"
    return f"{IPTC}/composite"


def build_manifest(
    output: Path,
    ingredients: list[Ingredient],
    command: str | None,
    version: str,
) -> dict[str, Any]:
    """The manifest JSON describing this artifact."""
    return {
        "claim_generator_info": [{"name": "fromwhere", "version": version}],
        "title": output.name,
        "format": media_type(output),
        "assertions": [
            {
                "label": "c2pa.actions.v2",
                "data": {
                    "actions": [
                        {
                            "action": "c2pa.created",
                            "softwareAgent": {
                                "name": "fromwhere",
                                "version": version,
                            },
                            "digitalSourceType": digital_source_type(
                                ingredients
                            ),
                            "description": command or "Produce artifact",
                        }
                    ]
                },
            },
            {
                # fromwhere's own record: enough to check the artifact against its
                # inputs without the repository the record lives in.
                "label": COMPOSITION_ASSERTION,
                "data": {
                    "command": command,
                    "inputs": [
                        {
                            "path": i.relative,
                            "digest": i.digest,
                            "kind": i.kind,
                            "mediaType": i.media_type,
                            "hasCredentials": i.has_credentials,
                        }
                        for i in ingredients
                    ],
                },
            },
        ],
    }


def ingredient_json(ingredient: Ingredient) -> dict[str, Any]:
    """Ingredient record for one input.

    `componentOf` is C2PA's term for "composited into" — the relationship an
    input actually has to the artifact, as opposed to `parentOf` (edited from)
    or `inputTo` (fed to a model).
    """
    return {
        "title": Path(ingredient.relative).name,
        "format": ingredient.media_type,
        "relationship": "componentOf",
    }


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def _signer_for(private_key: Any) -> tuple[Any, Callable[[bytes], bytes]]:
    """Pick the signing algorithm from the key, and return a matching callback.

    The algorithm is a property of the key, not a choice: signing an ECDSA key
    with RSA-PSS padding produces bytes that verify as nothing, and c2pa reports
    it only as "COSE signature invalid" — so getting this wrong is expensive to
    diagnose. Supporting whatever key a user brings (or another tool generated)
    means reading it off the key itself.
    """
    from c2pa import C2paSigningAlg
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import (
        ec,
        ed25519,
        padding,
        rsa,
    )
    from cryptography.hazmat.primitives.asymmetric.utils import (
        decode_dss_signature,
    )

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
        # Declared up front so the three branches are not read as narrowing to
        # whichever digest happens to come first.
        digest: hashes.HashAlgorithm
        if curve == "secp256r1":
            digest, alg, size = hashes.SHA256(), C2paSigningAlg.ES256, 32
        elif curve == "secp384r1":
            digest, alg, size = hashes.SHA384(), C2paSigningAlg.ES384, 48
        elif curve == "secp521r1":
            digest, alg, size = hashes.SHA512(), C2paSigningAlg.ES512, 66
        else:
            raise SigningError(
                f"unsupported elliptic curve for signing: {curve}"
            )

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
    ingredients: int
    machine_generated: bool
    warnings: list[str] = field(default_factory=list)


def sign_artifact(
    output: Path,
    inputs: list[Input],
    root: Path,
    *,
    cert: Path | None = None,
    key: Path | None = None,
    command: str | None = None,
    version: str = "0.1.0",
) -> SignResult:
    """Embed a signed manifest in an artifact, in place.

    In place, and therefore *before* the artifact is hashed for the record:
    embedding changes the bytes, so a hash taken beforehand would describe a
    file that no longer exists and every signed artifact would read as modified
    the moment it was written.
    """
    from c2pa import Builder, Context, Signer

    identity = resolve_identity(cert, key)
    ingredients = collect_ingredients(inputs, Path(root))
    manifest = build_manifest(Path(output), ingredients, command, version)

    from cryptography.hazmat.primitives import serialization

    private_key = serialization.load_pem_private_key(
        identity.private_key_pem, password=None
    )
    algorithm, sign_bytes = _signer_for(private_key)

    warnings: list[str] = []
    target = Path(output)
    signed = target.with_suffix(target.suffix + ".signed")

    try:
        with Context() as ctx:
            with Signer.from_callback(
                sign_bytes, algorithm, identity.cert_chain, identity.tsa_url
            ) as signer:
                with Builder(json.dumps(manifest), ctx) as builder:
                    for ingredient in ingredients:
                        try:
                            with ingredient.path.open("rb") as handle:
                                builder.add_ingredient(
                                    ingredient_json(ingredient),
                                    ingredient.media_type,
                                    handle,
                                )
                        except Exception as exc:  # noqa: BLE001
                            # An input we cannot record is worth saying out
                            # loud; the manifest would otherwise understate what
                            # the artifact is made of.
                            warnings.append(
                                f"could not record {ingredient.relative} as an "
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
        ingredients=len(ingredients),
        machine_generated=any(i.machine_generated for i in ingredients),
        warnings=warnings,
    )
