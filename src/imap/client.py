"""Gmail IMAP client with OAuth2 authentication."""

import imaplib
import re
import ssl
from typing import Any

from src.auth.oauth import GmailOAuth


class IMAPConnectionError(Exception):
    """Raised when IMAP connection fails."""

    pass


class IMAPAuthenticationError(Exception):
    """Raised when IMAP authentication fails."""

    pass


class GmailIMAPClient:
    """Gmail IMAP client with OAuth2 authentication.

    Provides a high-level interface for Gmail IMAP operations with
    automatic OAuth2 authentication using XOAUTH2 mechanism.
    """

    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993

    # Gmail-specific IMAP extensions
    GMAIL_EXTENSION_MSGID = "X-GM-MSGID"
    GMAIL_EXTENSION_THRID = "X-GM-THRID"
    GMAIL_EXTENSION_LABELS = "X-GM-LABELS"

    def __init__(self, oauth_handler: GmailOAuth, email_address: str) -> None:
        """Initialize client with OAuth handler and target email.

        Args:
            oauth_handler: Configured GmailOAuth instance.
            email_address: Gmail address to authenticate.
        """
        self.oauth_handler = oauth_handler
        self.email_address = email_address
        self._connection: imaplib.IMAP4_SSL | None = None
        self._selected_folder: str | None = None

    def connect(self) -> None:
        """Establish SSL connection to Gmail IMAP server.

        Raises:
            IMAPConnectionError: If connection fails.
        """
        if self._connection is not None:
            return  # Already connected

        try:
            # Create SSL context with certificate verification
            ssl_context = ssl.create_default_context()

            self._connection = imaplib.IMAP4_SSL(
                host=self.IMAP_HOST,
                port=self.IMAP_PORT,
                ssl_context=ssl_context,
            )
        except Exception as e:
            raise IMAPConnectionError(f"Failed to connect to Gmail IMAP: {e}") from e

    def authenticate(self) -> None:
        """Authenticate using XOAUTH2 mechanism.

        Raises:
            IMAPAuthenticationError: If authentication fails.
            IMAPConnectionError: If not connected.
        """
        if self._connection is None:
            raise IMAPConnectionError("Not connected. Call connect() first.")

        try:
            # Generate XOAUTH2 string
            auth_string = self.oauth_handler.generate_xoauth2_string(self.email_address)

            # Authenticate using XOAUTH2
            # The lambda is required by imaplib.authenticate()
            self._connection.authenticate(
                "XOAUTH2",
                lambda _: auth_string.encode(),
            )
        except imaplib.IMAP4.error as e:
            raise IMAPAuthenticationError(f"IMAP authentication failed: {e}") from e
        except Exception as e:
            raise IMAPAuthenticationError(f"Authentication error: {e}") from e

    def disconnect(self) -> None:
        """Properly close IMAP connection."""
        if self._connection is not None:
            try:
                # Close selected mailbox if any
                if self._selected_folder:
                    self._connection.close()
                # Logout from server
                self._connection.logout()
            except Exception:
                pass  # Ignore errors during disconnect
            finally:
                self._connection = None
                self._selected_folder = None

    def select_folder(self, folder: str = "[Gmail]/All Mail", readonly: bool = True) -> int:
        """Select mailbox folder.

        Args:
            folder: IMAP folder name. Gmail uses "[Gmail]/All Mail" for all messages.
            readonly: If True, open in read-only mode.

        Returns:
            Number of messages in the folder.

        Raises:
            IMAPConnectionError: If not connected or selection fails.
        """
        self._ensure_connected()

        try:
            status, data = self._connection.select(folder, readonly=readonly)  # type: ignore
            if status != "OK":
                raise IMAPConnectionError(f"Failed to select folder {folder}: {data}")

            self._selected_folder = folder

            # Parse message count from response
            count = int(data[0].decode() if isinstance(data[0], bytes) else data[0])
            return count

        except imaplib.IMAP4.error as e:
            raise IMAPConnectionError(f"Error selecting folder {folder}: {e}") from e

    def list_folders(self) -> list[str]:
        """List all available IMAP folders/labels.

        Returns:
            List of folder names.

        Raises:
            IMAPConnectionError: If not connected.
        """
        self._ensure_connected()

        try:
            status, data = self._connection.list()  # type: ignore
            if status != "OK":
                return []

            folders = []
            for item in data:
                if item is None:
                    continue
                # Parse folder name from IMAP LIST response
                # Format: (\\Flags) "delimiter" "folder_name"
                if isinstance(item, bytes):
                    item = item.decode("utf-8", errors="replace")
                match = re.search(r'"[^"]*" "(.*)"$|"[^"]*" (.*)$', item)
                if match:
                    folder_name = match.group(1) or match.group(2)
                    folders.append(folder_name.strip('"'))

            return folders

        except Exception as e:
            raise IMAPConnectionError(f"Error listing folders: {e}") from e

    def search(self, criteria: str) -> list[int]:
        """Search for messages matching criteria.

        Args:
            criteria: IMAP search criteria string.
                Supports Gmail X-GM-RAW extension for Gmail search syntax.

        Returns:
            List of message UIDs matching criteria.

        Raises:
            IMAPConnectionError: If not connected or no folder selected.
        """
        self._ensure_connected()
        self._ensure_folder_selected()

        try:
            status, data = self._connection.uid("SEARCH", None, criteria)  # type: ignore
            if status != "OK":
                return []

            # Parse UIDs from response
            if data[0]:
                uid_bytes = data[0]
                if isinstance(uid_bytes, bytes):
                    uid_bytes = uid_bytes.decode()
                return [int(uid) for uid in uid_bytes.split()]

            return []

        except Exception as e:
            raise IMAPConnectionError(f"Search failed: {e}") from e

    def fetch(self, uid: int, parts: str) -> dict[str, Any]:
        """Fetch message data by UID.

        Args:
            uid: Message UID.
            parts: IMAP FETCH parts specification
                (e.g., "RFC822", "BODY[HEADER]", "BODYSTRUCTURE").

        Returns:
            Dictionary with fetched data.

        Raises:
            IMAPConnectionError: If fetch fails.
        """
        self._ensure_connected()
        self._ensure_folder_selected()

        try:
            status, data = self._connection.uid("FETCH", str(uid), parts)  # type: ignore
            if status != "OK" or not data or data[0] is None:
                raise IMAPConnectionError(f"Failed to fetch UID {uid}")

            return self._parse_fetch_response(data)

        except imaplib.IMAP4.error as e:
            raise IMAPConnectionError(f"Fetch error for UID {uid}: {e}") from e

    def fetch_raw_email(self, uid: int) -> bytes:
        """Fetch complete raw email by UID.

        Args:
            uid: Message UID.

        Returns:
            Raw email bytes (RFC822 format).
        """
        result = self.fetch(uid, "(RFC822)")
        return result.get("RFC822", b"")

    def fetch_headers(self, uid: int) -> bytes:
        """Fetch only email headers by UID.

        Args:
            uid: Message UID.

        Returns:
            Raw header bytes.
        """
        result = self.fetch(uid, "(BODY[HEADER])")
        return result.get("BODY[HEADER]", b"")

    def fetch_bodystructure(self, uid: int) -> Any:
        """Fetch BODYSTRUCTURE for attachment analysis.

        Args:
            uid: Message UID.

        Returns:
            Parsed BODYSTRUCTURE tuple.
        """
        result = self.fetch(uid, "(BODYSTRUCTURE)")
        return result.get("BODYSTRUCTURE")

    def fetch_gmail_metadata(self, uid: int) -> dict[str, Any]:
        """Fetch Gmail-specific metadata (X-GM-MSGID, X-GM-THRID, X-GM-LABELS).

        Args:
            uid: Message UID.

        Returns:
            Dictionary with gmail_message_id, gmail_thread_id, and labels.
        """
        parts = f"({self.GMAIL_EXTENSION_MSGID} {self.GMAIL_EXTENSION_THRID} {self.GMAIL_EXTENSION_LABELS})"
        result = self.fetch(uid, parts)

        return {
            "gmail_message_id": result.get(self.GMAIL_EXTENSION_MSGID),
            "gmail_thread_id": result.get(self.GMAIL_EXTENSION_THRID),
            "labels": result.get(self.GMAIL_EXTENSION_LABELS, []),
        }

    def fetch_part(self, uid: int, part_number: str) -> bytes:
        """Fetch specific MIME part by part number.

        Args:
            uid: Message UID.
            part_number: MIME part number (e.g., "1", "2.1").

        Returns:
            Raw part content (may be encoded).
        """
        result = self.fetch(uid, f"(BODY[{part_number}])")
        return result.get(f"BODY[{part_number}]", b"")

    def append(
        self,
        folder: str,
        email_data: bytes,
        flags: list[str] | None = None,
        date_time: Any = None,
    ) -> int | None:
        """Upload email to folder via IMAP APPEND.

        Args:
            folder: Target folder name.
            email_data: Raw email bytes (RFC822 format).
            flags: List of IMAP flags (e.g., ["\\Seen"]).
            date_time: Internal date for message.

        Returns:
            UID of appended message, or None if unknown.

        Raises:
            IMAPConnectionError: If append fails.
        """
        self._ensure_connected()

        try:
            flag_str = " ".join(flags) if flags else None
            status, data = self._connection.append(  # type: ignore
                folder,
                flag_str,
                date_time,
                email_data,
            )

            if status != "OK":
                raise IMAPConnectionError(f"APPEND failed: {data}")

            # Try to extract UID from response (format varies)
            # Gmail typically returns: [b'[APPENDUID uidvalidity uid] Success']
            if data and data[0]:
                response = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
                match = re.search(r"APPENDUID \d+ (\d+)", response)
                if match:
                    return int(match.group(1))

            return None

        except imaplib.IMAP4.error as e:
            raise IMAPConnectionError(f"APPEND error: {e}") from e

    def store_labels(self, uid: int, labels: list[str], action: str = "+") -> bool:
        """Add or remove Gmail labels from message.

        Args:
            uid: Message UID.
            labels: List of labels to add/remove.
            action: "+" to add, "-" to remove.

        Returns:
            True if successful.

        Raises:
            IMAPConnectionError: If operation fails.
        """
        self._ensure_connected()
        self._ensure_folder_selected()

        try:
            # Format labels for X-GM-LABELS
            label_str = " ".join(f'"{label}"' for label in labels)
            command = f"{action}X-GM-LABELS"

            status, _ = self._connection.uid("STORE", str(uid), command, f"({label_str})")  # type: ignore
            return status == "OK"

        except Exception as e:
            raise IMAPConnectionError(f"Failed to store labels: {e}") from e

    def copy_to_folder(self, uid: int, folder: str) -> bool:
        """Copy message to another folder.

        Args:
            uid: Message UID.
            folder: Target folder name.

        Returns:
            True if successful.
        """
        self._ensure_connected()
        self._ensure_folder_selected()

        try:
            status, _ = self._connection.uid("COPY", str(uid), folder)  # type: ignore
            return status == "OK"
        except Exception:
            return False

    def delete_message(self, uid: int) -> bool:
        """Mark message as deleted (will be removed on EXPUNGE).

        Args:
            uid: Message UID.

        Returns:
            True if successful.
        """
        self._ensure_connected()
        self._ensure_folder_selected()

        try:
            status, _ = self._connection.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")  # type: ignore
            return status == "OK"
        except Exception:
            return False

    def move_to_trash(self, uid: int) -> bool:
        """Move message to Gmail Trash.

        Args:
            uid: Message UID.

        Returns:
            True if successful.
        """
        # Copy to trash, then delete from current folder
        if self.copy_to_folder(uid, "[Gmail]/Trash"):
            return self.delete_message(uid)
        return False

    def expunge(self) -> None:
        """Permanently remove messages marked as deleted."""
        self._ensure_connected()
        self._ensure_folder_selected()

        try:
            self._connection.expunge()  # type: ignore
        except Exception:
            pass

    def _ensure_connected(self) -> None:
        """Ensure client is connected.

        Raises:
            IMAPConnectionError: If not connected.
        """
        if self._connection is None:
            raise IMAPConnectionError("Not connected. Call connect() and authenticate() first.")

    def _ensure_folder_selected(self) -> None:
        """Ensure a folder is selected.

        Raises:
            IMAPConnectionError: If no folder selected.
        """
        if self._selected_folder is None:
            raise IMAPConnectionError("No folder selected. Call select_folder() first.")

    def _parse_fetch_response(self, data: list[Any]) -> dict[str, Any]:
        """Parse IMAP FETCH response into dictionary.

        Args:
            data: Raw FETCH response data.

        Returns:
            Dictionary mapping part names to values.
        """
        result: dict[str, Any] = {}

        for item in data:
            if item is None:
                continue

            if isinstance(item, tuple):
                # Tuple: (header_info, body_content)
                header = item[0].decode() if isinstance(item[0], bytes) else str(item[0])
                body = item[1] if len(item) > 1 else None

                # Parse header info
                self._parse_fetch_header(header, body, result)

            elif isinstance(item, bytes):
                # Closing paren or other marker - ignore
                pass

        return result

    def _parse_fetch_header(
        self, header: str, body: Any, result: dict[str, Any]
    ) -> None:
        """Parse FETCH response header and extract values.

        Args:
            header: Header string from FETCH response.
            body: Body content if present.
            result: Dictionary to populate with parsed values.
        """
        # Extract X-GM-MSGID
        match = re.search(r"X-GM-MSGID (\d+)", header)
        if match:
            result["X-GM-MSGID"] = int(match.group(1))

        # Extract X-GM-THRID
        match = re.search(r"X-GM-THRID (\d+)", header)
        if match:
            result["X-GM-THRID"] = int(match.group(1))

        # Extract X-GM-LABELS
        match = re.search(r'X-GM-LABELS \(([^)]*)\)', header)
        if match:
            labels_str = match.group(1)
            # Parse quoted and unquoted labels
            labels = re.findall(r'"([^"]+)"|(\S+)', labels_str)
            result["X-GM-LABELS"] = [l[0] or l[1] for l in labels if l[0] or l[1]]

        # Extract BODYSTRUCTURE
        match = re.search(r"BODYSTRUCTURE (\(.*\))", header)
        if match:
            # Store raw string - will need specialized parsing
            result["BODYSTRUCTURE"] = match.group(1)

        # Extract RFC822 or BODY parts
        if body is not None:
            if "RFC822" in header:
                result["RFC822"] = body
            elif "BODY[HEADER]" in header:
                result["BODY[HEADER]"] = body
            else:
                # Try to match BODY[x] patterns
                match = re.search(r"BODY\[([^\]]*)\]", header)
                if match:
                    key = f"BODY[{match.group(1)}]"
                    result[key] = body

    def __enter__(self) -> "GmailIMAPClient":
        """Context manager entry - connect and authenticate."""
        self.connect()
        self.authenticate()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit - disconnect."""
        self.disconnect()
