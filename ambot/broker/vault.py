"""
AES-256-GCM key vault for storing Binance API credentials.

Security properties:
- Encryption: AES-256-GCM (authenticated encryption)
- Each encrypt call uses a fresh random 96-bit nonce
- Stored format: base64(nonce || ciphertext || tag)
- Master key is loaded from environment variable VAULT_MASTER_KEY_HEX
- Plaintext keys are NEVER logged, written to disk, or stored in variables
  with long lifetimes — callers should consume the returned tuple immediately.
"""
from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ambot.exceptions import KeyDecryptionError, VaultError


class KeyVault:
    """AES-256-GCM symmetric vault for Binance API key pairs."""

    NONCE_SIZE = 12  # 96-bit GCM nonce

    def __init__(self, master_key_hex: str) -> None:
        """
        Parameters
        ----------
        master_key_hex:
            64-character hex string representing 32 bytes (256 bits).
            Generate with: python -c "import secrets; print(secrets.token_hex(32))"
        """
        try:
            raw = bytes.fromhex(master_key_hex)
        except ValueError as exc:
            raise VaultError("master_key_hex is not valid hexadecimal") from exc

        if len(raw) != 32:
            raise VaultError(
                f"Master key must be exactly 32 bytes (256 bits), got {len(raw)} bytes"
            )

        self._aesgcm = AESGCM(raw)

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string.

        Returns
        -------
        str
            base64-encoded blob: nonce (12 bytes) || ciphertext+tag
        """
        nonce = os.urandom(self.NONCE_SIZE)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        blob = base64.b64encode(nonce + ciphertext).decode("ascii")
        return blob

    def decrypt(self, blob: str) -> str:
        """
        Decrypt a stored blob produced by :meth:`encrypt`.

        Raises
        ------
        KeyDecryptionError
            If the blob is malformed or has been tampered with.
        """
        try:
            raw = base64.b64decode(blob)
        except Exception as exc:
            raise KeyDecryptionError("Blob is not valid base64") from exc

        if len(raw) <= self.NONCE_SIZE:
            raise KeyDecryptionError("Blob is too short to contain a nonce and ciphertext")

        nonce = raw[: self.NONCE_SIZE]
        ciphertext = raw[self.NONCE_SIZE :]

        try:
            plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise KeyDecryptionError(
                "Decryption failed — blob may be tampered or master key is wrong"
            ) from exc

        return plaintext.decode("utf-8")

    def encrypt_keypair(self, api_key: str, api_secret: str) -> tuple[str, str]:
        """
        Encrypt an API key and secret together.

        Returns
        -------
        tuple[str, str]
            (encrypted_api_key_blob, encrypted_api_secret_blob)
        """
        return self.encrypt(api_key), self.encrypt(api_secret)

    def decrypt_keypair(self, encrypted_key: str, encrypted_secret: str) -> tuple[str, str]:
        """
        Decrypt an API key pair.

        Returns
        -------
        tuple[str, str]
            (plaintext_api_key, plaintext_api_secret)

        Security note: consume the return value immediately.
        Do not store in module-level or instance-level variables.
        """
        return self.decrypt(encrypted_key), self.decrypt(encrypted_secret)
