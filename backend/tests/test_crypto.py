import pytest
from cryptography.fernet import InvalidToken

from app.security import crypto


def test_round_trip():
    plaintext = "hunter2-super-secret"
    ciphertext = crypto.encrypt(plaintext)
    assert ciphertext != plaintext.encode()
    assert crypto.decrypt(ciphertext) == plaintext


def test_ciphertext_not_deterministic():
    plaintext = "same-input-twice"
    assert crypto.encrypt(plaintext) != crypto.encrypt(plaintext)


def test_wrong_key_fails_to_decrypt(monkeypatch):
    from cryptography.fernet import Fernet

    ciphertext = crypto.encrypt("some credential")
    monkeypatch.setenv("APP_SECRET_KEY", Fernet.generate_key().decode())
    crypto.get_settings.cache_clear()
    with pytest.raises(InvalidToken):
        crypto.decrypt(ciphertext)
    crypto.get_settings.cache_clear()
