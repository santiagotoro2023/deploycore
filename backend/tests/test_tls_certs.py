from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.services import tls_certs


def _make_pair(*, not_valid_after: datetime | None = None) -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.local")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(not_valid_after or now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def test_matching_pair_is_valid():
    cert_pem, key_pem = _make_pair()
    info = tls_certs.validate_pair(cert_pem, key_pem)
    assert "test.local" in info.subject


def test_mismatched_key_is_rejected():
    cert_pem, _ = _make_pair()
    _, other_key_pem = _make_pair()
    with pytest.raises(tls_certs.InvalidCertificate, match="don't match"):
        tls_certs.validate_pair(cert_pem, other_key_pem)


def test_expired_certificate_is_rejected():
    cert_pem, key_pem = _make_pair(not_valid_after=datetime.now(timezone.utc) - timedelta(days=1))
    with pytest.raises(tls_certs.InvalidCertificate, match="expired"):
        tls_certs.validate_pair(cert_pem, key_pem)


def test_garbage_input_is_rejected():
    with pytest.raises(tls_certs.InvalidCertificate, match="certificate"):
        tls_certs.validate_pair(b"not a cert", b"not a key")


def test_write_and_read_round_trip(tmp_path):
    cert_pem, key_pem = _make_pair()
    tls_certs.write_pair(tmp_path, cert_pem, key_pem)
    info = tls_certs.read_uploaded_info(tmp_path)
    assert info is not None
    assert "test.local" in info.subject
    assert (tmp_path / tls_certs.KEY_FILENAME).stat().st_mode & 0o777 == 0o600


def test_read_uploaded_info_missing_returns_none(tmp_path):
    assert tls_certs.read_uploaded_info(tmp_path) is None
