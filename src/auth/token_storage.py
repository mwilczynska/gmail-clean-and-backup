"""Secure token storage with encryption."""

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class SecureTokenStorage:
    """Encrypted local storage for OAuth tokens.

    Uses Fernet symmetric encryption with a key derived from a password
    using PBKDF2. If no password is provided, uses a machine-specific
    key based on username and hostname.
    """

    SALT_SIZE = 16
    ITERATIONS = 480000  # OWASP 2023 recommendation for PBKDF2-SHA256

    def __init__(self, storage_path: Path, password: str | None = None) -> None:
        """Initialize storage with path and optional password.

        Args:
            storage_path: Path to store encrypted token file.
            password: Optional password for encryption. If not provided,
                uses a machine-specific default (less secure but convenient).
        """
        self.storage_path = Path(storage_path)
        self._password = password or self._get_default_password()

    def _get_default_password(self) -> str:
        """Generate a machine-specific default password.

        This is less secure than a user-provided password but prevents
        casual access to tokens. The token file is still protected by
        filesystem permissions.
        """
        import getpass
        import platform

        return f"{getpass.getuser()}@{platform.node()}_gmail_clean_backup"

    def _derive_key(self, salt: bytes) -> bytes:
        """Derive encryption key from password using PBKDF2.

        Args:
            salt: Random salt for key derivation.

        Returns:
            32-byte key suitable for Fernet.
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self.ITERATIONS,
        )
        return base64.urlsafe_b64encode(kdf.derive(self._password.encode()))

    def save(self, token_data: dict[str, Any]) -> None:
        """Encrypt and save token data.

        Args:
            token_data: Dictionary containing OAuth token information.
        """
        # Generate new random salt for each save
        salt = os.urandom(self.SALT_SIZE)
        key = self._derive_key(salt)
        fernet = Fernet(key)

        # Serialize and encrypt
        json_data = json.dumps(token_data).encode()
        encrypted = fernet.encrypt(json_data)

        # Store salt + encrypted data
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, "wb") as f:
            f.write(salt + encrypted)

        # Set restrictive permissions on Unix-like systems
        try:
            os.chmod(self.storage_path, 0o600)
        except (OSError, AttributeError):
            pass  # Windows doesn't support chmod the same way

    def load(self) -> dict[str, Any] | None:
        """Load and decrypt token data.

        Returns:
            Decrypted token dictionary, or None if file doesn't exist
            or decryption fails.
        """
        if not self.storage_path.exists():
            return None

        try:
            with open(self.storage_path, "rb") as f:
                data = f.read()

            # Extract salt and encrypted data
            salt = data[: self.SALT_SIZE]
            encrypted = data[self.SALT_SIZE :]

            # Derive key and decrypt
            key = self._derive_key(salt)
            fernet = Fernet(key)
            decrypted = fernet.decrypt(encrypted)

            return json.loads(decrypted.decode())

        except Exception:
            # Decryption failed - token may be corrupted or password changed
            return None

    def delete(self) -> bool:
        """Delete stored token file.

        Returns:
            True if file was deleted, False if it didn't exist.
        """
        if self.storage_path.exists():
            self.storage_path.unlink()
            return True
        return False

    def exists(self) -> bool:
        """Check if token file exists.

        Returns:
            True if token file exists.
        """
        return self.storage_path.exists()
