"""Unit tests for AES-256-GCM key vault."""
from __future__ import annotations

import pytest

from ambot.broker.vault import KeyVault
from ambot.exceptions import KeyDecryptionError, VaultError


VALID_KEY = "a" * 64  # 32 bytes hex


class TestKeyVault:
    def test_encrypt_produces_non_empty_blob(self):
        vault = KeyVault(VALID_KEY)
        blob = vault.encrypt("my_api_key")
        assert blob
        assert blob != "my_api_key"

    def test_decrypt_roundtrip(self):
        vault = KeyVault(VALID_KEY)
        plaintext = "BINANCE_API_KEY_12345"
        blob = vault.encrypt(plaintext)
        assert vault.decrypt(blob) == plaintext

    def test_different_nonces_per_call(self):
        """Each encrypt call must produce a different blob (random nonce)."""
        vault = KeyVault(VALID_KEY)
        b1 = vault.encrypt("same_key")
        b2 = vault.encrypt("same_key")
        assert b1 != b2

    def test_tampered_ciphertext_raises(self):
        vault = KeyVault(VALID_KEY)
        blob = vault.encrypt("key")
        # Corrupt the last 4 bytes
        tampered = blob[:-4] + "XXXX"
        with pytest.raises(KeyDecryptionError):
            vault.decrypt(tampered)

    def test_wrong_master_key_raises(self):
        vault1 = KeyVault(VALID_KEY)
        vault2 = KeyVault("b" * 64)  # Different master key
        blob = vault1.encrypt("secret")
        with pytest.raises(KeyDecryptionError):
            vault2.decrypt(blob)

    def test_invalid_key_length_raises(self):
        with pytest.raises(VaultError):
            KeyVault("abc123")  # Too short

    def test_invalid_hex_raises(self):
        with pytest.raises(VaultError):
            KeyVault("x" * 64)  # Not valid hex

    def test_encrypt_keypair_roundtrip(self):
        vault = KeyVault(VALID_KEY)
        api_key = "api_key_abc"
        api_secret = "secret_xyz"
        enc_k, enc_s = vault.encrypt_keypair(api_key, api_secret)
        dec_k, dec_s = vault.decrypt_keypair(enc_k, enc_s)
        assert dec_k == api_key
        assert dec_s == api_secret

    def test_encrypt_empty_string(self):
        vault = KeyVault(VALID_KEY)
        blob = vault.encrypt("")
        assert vault.decrypt(blob) == ""

    def test_encrypt_unicode(self):
        vault = KeyVault(VALID_KEY)
        text = "🔑 key with unicode"
        assert vault.decrypt(vault.encrypt(text)) == text
