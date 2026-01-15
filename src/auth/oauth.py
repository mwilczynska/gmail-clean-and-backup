"""OAuth2 authentication handler for Gmail IMAP access."""

import base64
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from src.auth.token_storage import SecureTokenStorage


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    pass


class GmailOAuth:
    """OAuth2 authentication handler for Gmail IMAP access.

    Handles the OAuth2 flow for Gmail, including token storage,
    refresh, and XOAUTH2 string generation for IMAP authentication.
    """

    # Gmail IMAP requires this scope for full access
    DEFAULT_SCOPES = ["https://mail.google.com/"]

    # Refresh token when less than this many seconds remain
    REFRESH_THRESHOLD_SECONDS = 300  # 5 minutes

    def __init__(
        self,
        credentials_file: Path,
        token_file: Path,
        scopes: list[str] | None = None,
        password: str | None = None,
    ) -> None:
        """Initialize OAuth handler.

        Args:
            credentials_file: Path to OAuth client credentials JSON from
                Google Cloud Console.
            token_file: Path to store encrypted OAuth tokens.
            scopes: OAuth scopes to request. Defaults to Gmail full access.
            password: Optional password for token encryption.
        """
        self.credentials_file = Path(credentials_file)
        self.token_storage = SecureTokenStorage(token_file, password)
        self.scopes = scopes or self.DEFAULT_SCOPES
        self._credentials: Credentials | None = None

    def get_credentials(self) -> Credentials:
        """Get valid OAuth credentials, refreshing or re-authenticating as needed.

        Returns:
            Valid Google OAuth2 credentials.

        Raises:
            AuthenticationError: If authentication fails.
        """
        # Try to load existing credentials
        creds = self._load_credentials()

        if creds and creds.valid:
            self._credentials = creds
            return creds

        # Try to refresh expired credentials
        if creds and creds.expired and creds.refresh_token:
            try:
                creds = self._refresh_credentials(creds)
                self._credentials = creds
                return creds
            except Exception as e:
                # Refresh failed, need to re-authenticate
                raise AuthenticationError(
                    f"Token refresh failed: {e}. Please re-authenticate."
                ) from e

        # No valid credentials, run OAuth flow
        creds = self._run_oauth_flow()
        self._credentials = creds
        return creds

    def _load_credentials(self) -> Credentials | None:
        """Load credentials from encrypted storage.

        Returns:
            Credentials object or None if not found/invalid.
        """
        from datetime import datetime

        token_data = self.token_storage.load()
        if not token_data:
            return None

        try:
            creds = Credentials(
                token=token_data.get("token"),
                refresh_token=token_data.get("refresh_token"),
                token_uri=token_data.get("token_uri"),
                client_id=token_data.get("client_id"),
                client_secret=token_data.get("client_secret"),
                scopes=token_data.get("scopes"),
            )

            # Restore expiry time if saved
            expiry_str = token_data.get("expiry")
            if expiry_str:
                expiry = datetime.fromisoformat(expiry_str)
                # Google's library uses naive datetimes (UTC assumed)
                # Remove timezone info if present
                if expiry.tzinfo is not None:
                    expiry = expiry.replace(tzinfo=None)
                creds.expiry = expiry
            else:
                # No expiry saved - assume token is expired to force refresh
                # Use naive datetime to match Google's library
                creds.expiry = datetime.utcnow()

            return creds
        except Exception:
            return None

    def _save_credentials(self, creds: Credentials) -> None:
        """Save credentials to encrypted storage.

        Args:
            creds: Credentials to save.
        """
        token_data: dict[str, Any] = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else self.scopes,
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
        }
        self.token_storage.save(token_data)

    def _refresh_credentials(self, creds: Credentials) -> Credentials:
        """Refresh expired credentials.

        Args:
            creds: Expired credentials with valid refresh token.

        Returns:
            Refreshed credentials.

        Raises:
            Exception: If refresh fails.
        """
        creds.refresh(Request())
        self._save_credentials(creds)
        return creds

    def _run_oauth_flow(self) -> Credentials:
        """Run the OAuth2 authorization flow.

        Opens a browser for user authentication and handles the callback.

        Returns:
            New credentials from completed OAuth flow.

        Raises:
            AuthenticationError: If the flow fails.
        """
        if not self.credentials_file.exists():
            raise AuthenticationError(
                f"Credentials file not found: {self.credentials_file}\n"
                "Download OAuth credentials from Google Cloud Console."
            )

        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_file),
                scopes=self.scopes,
            )

            # Run local server for OAuth callback
            # Use port 0 for auto-selection
            creds = flow.run_local_server(
                port=0,
                prompt="consent",
                access_type="offline",  # Get refresh token
            )

            self._save_credentials(creds)
            return creds

        except Exception as e:
            raise AuthenticationError(f"OAuth flow failed: {e}") from e

    def refresh_if_needed(self) -> Credentials:
        """Refresh credentials if they're expired or about to expire.

        Returns:
            Valid credentials.
        """
        if self._credentials is None:
            return self.get_credentials()

        # Check if refresh needed
        if self._credentials.expired:
            return self._refresh_credentials(self._credentials)

        # Proactively refresh if close to expiry
        if self._credentials.expiry:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            # Handle both aware and naive datetime
            expiry = self._credentials.expiry
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)

            seconds_remaining = (expiry - now).total_seconds()
            if seconds_remaining < self.REFRESH_THRESHOLD_SECONDS:
                return self._refresh_credentials(self._credentials)

        return self._credentials

    def generate_xoauth2_string(self, email: str) -> bytes:
        """Generate SASL XOAUTH2 authentication string for IMAP.

        This format is required by Gmail's IMAP server for OAuth2 authentication.
        See: https://developers.google.com/gmail/imap/xoauth2-protocol

        Args:
            email: Gmail address to authenticate.

        Returns:
            Raw XOAUTH2 authentication bytes (imaplib will base64-encode).

        Raises:
            AuthenticationError: If no valid credentials available.
        """
        creds = self.refresh_if_needed()

        if not creds.token:
            raise AuthenticationError("No access token available")

        # XOAUTH2 format: user={email}\x01auth=Bearer {token}\x01\x01
        auth_string = f"user={email}\x01auth=Bearer {creds.token}\x01\x01"
        return auth_string.encode()

    def revoke(self) -> bool:
        """Revoke stored credentials and delete token file.

        Returns:
            True if credentials were revoked/deleted.
        """
        # TODO: Actually revoke token with Google if needed
        return self.token_storage.delete()

    @property
    def is_authenticated(self) -> bool:
        """Check if valid credentials are available.

        Returns:
            True if authenticated with valid or refreshable credentials.
        """
        try:
            self.get_credentials()
            return True
        except AuthenticationError:
            return False
