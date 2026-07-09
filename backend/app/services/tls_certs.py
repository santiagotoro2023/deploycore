from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
)

CERT_FILENAME = "uploaded-cert.pem"
KEY_FILENAME = "uploaded-key.pem"


class InvalidCertificate(ValueError):
    """Raised for anything wrong with an uploaded cert/key pair, its
    message is safe to show directly to the admin uploading it."""


@dataclass
class CertInfo:
    subject: str
    not_valid_after: datetime


def _public_key_fingerprint(key) -> bytes:
    """Compares by the encoded public key itself rather than by RSA/EC
    modulus, so this works the same regardless of key algorithm (RSA, EC,
    Ed25519, ...) without a per-algorithm branch."""
    return key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def validate_pair(cert_pem: bytes, key_pem: bytes) -> CertInfo:
    """Parses the uploaded cert/key, confirms they're a matching pair and
    the cert isn't expired, and returns a bit of info worth showing back
    in Settings. Raises InvalidCertificate with a message safe to surface
    to the admin who uploaded it."""
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
    except ValueError:
        raise InvalidCertificate("That doesn't look like a valid PEM certificate.")
    try:
        key = load_pem_private_key(key_pem, password=None)
    except (ValueError, TypeError):
        raise InvalidCertificate(
            "That doesn't look like a valid, unencrypted PEM private key "
            "(password-protected keys aren't supported)."
        )

    if _public_key_fingerprint(cert.public_key()) != _public_key_fingerprint(key.public_key()):
        raise InvalidCertificate("The certificate and private key don't match.")

    not_valid_after = cert.not_valid_after_utc
    if not_valid_after < datetime.now(timezone.utc):
        raise InvalidCertificate(f"That certificate expired on {not_valid_after:%Y-%m-%d}.")

    return CertInfo(subject=cert.subject.rfc4514_string(), not_valid_after=not_valid_after)


def read_uploaded_info(cert_dir: Path) -> CertInfo | None:
    cert_path = cert_dir / CERT_FILENAME
    if not cert_path.exists():
        return None
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    return CertInfo(subject=cert.subject.rfc4514_string(), not_valid_after=cert.not_valid_after_utc)


def write_pair(cert_dir: Path, cert_pem: bytes, key_pem: bytes) -> None:
    cert_dir.mkdir(parents=True, exist_ok=True)
    (cert_dir / CERT_FILENAME).write_bytes(cert_pem)
    key_path = cert_dir / KEY_FILENAME
    key_path.write_bytes(key_pem)
    key_path.chmod(0o600)
