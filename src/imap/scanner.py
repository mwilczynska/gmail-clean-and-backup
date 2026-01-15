"""Email scanner for attachment detection without full download."""

import re
from datetime import datetime
from email import policy
from email.header import decode_header
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from typing import TYPE_CHECKING, Any

from src.models.email import (
    AttachmentInfo,
    EmailHeader,
    EmailScanResult,
    GmailMetadata,
    ScanStatistics,
)

if TYPE_CHECKING:
    from src.imap.client import GmailIMAPClient


class EmailScanner:
    """Scans emails for attachments without downloading full content.

    Uses IMAP BODYSTRUCTURE to efficiently identify attachments
    and their properties without fetching the actual content.
    """

    # Content types that indicate encryption
    ENCRYPTED_CONTENT_TYPES = {
        "application/pkcs7-mime",
        "application/x-pkcs7-mime",
        "application/pgp-encrypted",
        "multipart/encrypted",
    }

    # Content types that indicate a signature (not encryption)
    SIGNATURE_CONTENT_TYPES = {
        "application/pkcs7-signature",
        "application/x-pkcs7-signature",
        "application/pgp-signature",
        "multipart/signed",
    }

    def __init__(self, client: "GmailIMAPClient") -> None:
        """Initialize scanner with IMAP client.

        Args:
            client: Connected and authenticated GmailIMAPClient.
        """
        self.client = client
        self._parser = BytesParser(policy=policy.default)

    def scan_email(self, uid: int) -> EmailScanResult:
        """Scan single email and identify attachments.

        Args:
            uid: Message UID to scan.

        Returns:
            EmailScanResult with header, metadata, and attachment info.
        """
        # Fetch headers and BODYSTRUCTURE in one request
        fetch_parts = "(BODY[HEADER] BODYSTRUCTURE X-GM-MSGID X-GM-THRID X-GM-LABELS RFC822.SIZE)"
        result = self.client.fetch(uid, fetch_parts)

        # Parse headers
        header_bytes = result.get("BODY[HEADER]", b"")
        header = self._parse_headers(uid, header_bytes, result.get("RFC822.SIZE", 0))

        # Parse Gmail metadata
        gmail_metadata = GmailMetadata(
            gmail_message_id=result.get("X-GM-MSGID", 0),
            gmail_thread_id=result.get("X-GM-THRID", 0),
            labels=result.get("X-GM-LABELS", []),
        )

        # Parse BODYSTRUCTURE for attachments
        bodystructure = result.get("BODYSTRUCTURE", "")
        attachments, is_encrypted, mime_complexity = self._parse_bodystructure(bodystructure)

        # Update header with attachment info
        header.has_attachments = len(attachments) > 0

        return EmailScanResult(
            header=header,
            gmail_metadata=gmail_metadata,
            attachments=attachments,
            is_encrypted=is_encrypted,
            mime_complexity=mime_complexity,
        )

    def scan_batch(
        self,
        uids: list[int],
        progress_callback: Any | None = None,
    ) -> list[EmailScanResult]:
        """Scan multiple emails with progress reporting.

        Args:
            uids: List of message UIDs to scan.
            progress_callback: Optional callback(current, total, message).

        Returns:
            List of EmailScanResult for each UID.
        """
        results: list[EmailScanResult] = []

        for i, uid in enumerate(uids):
            if progress_callback:
                progress_callback(i + 1, len(uids), f"Scanning UID {uid}")

            try:
                result = self.scan_email(uid)
                results.append(result)
            except Exception as e:
                # Log error but continue with other emails
                if progress_callback:
                    progress_callback(i + 1, len(uids), f"Error scanning UID {uid}: {e}")

        return results

    def generate_statistics(self, results: list[EmailScanResult]) -> ScanStatistics:
        """Generate aggregate statistics from scan results.

        Args:
            results: List of scan results.

        Returns:
            ScanStatistics with aggregated data.
        """
        stats = ScanStatistics(
            total_emails=len(results),
            total_attachments=0,
            total_attachment_size=0,
            emails_with_inline_only=0,
            encrypted_emails_skipped=0,
            estimated_backup_size=0,
            by_content_type={},
            by_year={},
            by_sender={},
        )

        for result in results:
            # Count encrypted
            if result.is_encrypted:
                stats.encrypted_emails_skipped += 1
                continue

            # Count attachments
            strippable = result.strippable_attachments
            if not strippable and result.attachments:
                stats.emails_with_inline_only += 1
                continue

            stats.total_attachments += len(strippable)
            stats.total_attachment_size += result.strippable_size
            # Add estimated decoded size (accounts for base64 overhead)
            stats.estimated_backup_size += result.estimated_strippable_size

            # By content type
            for att in strippable:
                content_type = att.content_type.split(";")[0].strip().lower()
                stats.by_content_type[content_type] = (
                    stats.by_content_type.get(content_type, 0) + 1
                )

            # By year
            year = result.header.date.year
            stats.by_year[year] = stats.by_year.get(year, 0) + 1

            # By sender (extract domain)
            sender = result.header.sender
            if "@" in sender:
                domain = sender.split("@")[-1].lower().rstrip(">")
                stats.by_sender[domain] = stats.by_sender.get(domain, 0) + 1

        return stats

    def _parse_headers(self, uid: int, header_bytes: bytes, size: int) -> EmailHeader:
        """Parse email headers into EmailHeader object.

        Args:
            uid: Message UID.
            header_bytes: Raw header bytes.
            size: Message size in bytes.

        Returns:
            Parsed EmailHeader.
        """
        # Parse headers
        try:
            msg = self._parser.parsebytes(header_bytes, headersonly=True)
        except Exception:
            # Fallback for malformed headers
            msg = None

        # Extract fields with fallbacks
        message_id = ""
        subject = "(No Subject)"
        sender = "(Unknown)"
        recipients: list[str] = []
        date = datetime.now()
        in_reply_to = None
        references: list[str] = []

        if msg:
            message_id = msg.get("Message-ID", "") or ""
            subject = self._decode_header(msg.get("Subject", "")) or "(No Subject)"
            sender = self._decode_header(msg.get("From", "")) or "(Unknown)"

            # Parse recipients
            for field in ["To", "Cc"]:
                value = msg.get(field, "")
                if value:
                    recipients.extend(self._parse_address_list(value))

            # Parse date
            date_str = msg.get("Date", "")
            if date_str:
                try:
                    date = parsedate_to_datetime(date_str)
                except Exception:
                    pass

            # Threading headers
            in_reply_to = msg.get("In-Reply-To")
            refs = msg.get("References", "")
            if refs:
                references = refs.split()

        return EmailHeader(
            uid=uid,
            message_id=message_id,
            subject=subject,
            sender=sender,
            recipients=recipients,
            date=date,
            size=size,
            has_attachments=False,  # Will be updated after BODYSTRUCTURE parse
            in_reply_to=in_reply_to,
            references=references,
        )

    def _decode_header(self, value: str | None) -> str:
        """Decode RFC 2047 encoded header value.

        Args:
            value: Possibly encoded header value.

        Returns:
            Decoded string.
        """
        if not value:
            return ""

        try:
            decoded_parts = decode_header(value)
            result = []
            for part, charset in decoded_parts:
                if isinstance(part, bytes):
                    charset = charset or "utf-8"
                    try:
                        result.append(part.decode(charset, errors="replace"))
                    except (LookupError, UnicodeDecodeError):
                        result.append(part.decode("utf-8", errors="replace"))
                else:
                    result.append(part)
            return "".join(result)
        except Exception:
            return str(value)

    def _parse_address_list(self, value: str) -> list[str]:
        """Parse comma-separated email addresses.

        Args:
            value: Address list string.

        Returns:
            List of email addresses.
        """
        addresses = []
        # Simple split on comma, handle quoted names
        for addr in value.split(","):
            _, email = parseaddr(addr.strip())
            if email:
                addresses.append(email)
        return addresses

    def _parse_bodystructure(
        self, bodystructure: str | Any
    ) -> tuple[list[AttachmentInfo], bool, int]:
        """Parse IMAP BODYSTRUCTURE to identify attachments.

        Args:
            bodystructure: Raw BODYSTRUCTURE response.

        Returns:
            Tuple of (attachments, is_encrypted, mime_complexity).
        """
        attachments: list[AttachmentInfo] = []
        is_encrypted = False
        max_depth = 1

        if not bodystructure:
            return attachments, is_encrypted, max_depth

        # Convert to string if needed
        if isinstance(bodystructure, bytes):
            bodystructure = bodystructure.decode("utf-8", errors="replace")
        elif not isinstance(bodystructure, str):
            bodystructure = str(bodystructure)

        # Parse the structure recursively
        try:
            attachments, is_encrypted, max_depth = self._parse_structure_recursive(
                bodystructure, "", 1
            )
        except Exception:
            # Parsing failed - return empty
            pass

        return attachments, is_encrypted, max_depth

    def _parse_structure_recursive(
        self, structure: str, part_prefix: str, depth: int
    ) -> tuple[list[AttachmentInfo], bool, int]:
        """Recursively parse BODYSTRUCTURE.

        Args:
            structure: BODYSTRUCTURE string or substring.
            part_prefix: Current part number prefix.
            depth: Current nesting depth.

        Returns:
            Tuple of (attachments, is_encrypted, max_depth).
        """
        attachments: list[AttachmentInfo] = []
        is_encrypted = False
        max_depth = depth

        # Limit recursion depth
        if depth > 20:
            return attachments, is_encrypted, max_depth

        # Check for multipart
        structure = structure.strip()
        if structure.startswith("(("):
            # This is a multipart message
            # Extract subparts and multipart type
            parts, multipart_type = self._split_multipart(structure)

            # Check for encryption
            if multipart_type and multipart_type.lower() in ["encrypted", "signed"]:
                is_encrypted = multipart_type.lower() == "encrypted"

            # Process each part
            for i, part in enumerate(parts):
                part_num = f"{part_prefix}{i + 1}" if part_prefix else str(i + 1)
                sub_attachments, sub_encrypted, sub_depth = self._parse_structure_recursive(
                    part, part_num + ".", depth + 1
                )
                attachments.extend(sub_attachments)
                is_encrypted = is_encrypted or sub_encrypted
                max_depth = max(max_depth, sub_depth)

        elif structure.startswith("("):
            # This is a single part - parse it
            attachment = self._parse_single_part(structure, part_prefix.rstrip(".") or "1")
            if attachment:
                # Check if this part indicates encryption
                if attachment.content_type.lower() in self.ENCRYPTED_CONTENT_TYPES:
                    is_encrypted = True
                # Only add if it's an attachment (not inline text)
                if attachment.content_disposition in ["attachment", "inline"]:
                    attachments.append(attachment)

        return attachments, is_encrypted, max_depth

    def _split_multipart(self, structure: str) -> tuple[list[str], str | None]:
        """Split multipart BODYSTRUCTURE into parts.

        Args:
            structure: Multipart BODYSTRUCTURE string.

        Returns:
            Tuple of (list of part strings, multipart subtype).
        """
        parts: list[str] = []
        multipart_type: str | None = None

        # Remove outer parens
        structure = structure.strip()
        if structure.startswith("(") and structure.endswith(")"):
            structure = structure[1:-1]

        # Find each nested part by tracking paren depth
        depth = 0
        current = ""
        i = 0

        while i < len(structure):
            char = structure[i]

            if char == "(":
                depth += 1
                current += char
            elif char == ")":
                depth -= 1
                current += char
                if depth == 0 and current.strip():
                    parts.append(current.strip())
                    current = ""
            elif depth > 0:
                current += char
            elif char == '"':
                # String outside of parts - likely multipart type
                # Find closing quote
                end = structure.find('"', i + 1)
                if end > i:
                    multipart_type = structure[i + 1 : end]
                    i = end
            i += 1

        return parts, multipart_type

    def _parse_single_part(self, structure: str, part_number: str) -> AttachmentInfo | None:
        """Parse a single BODYSTRUCTURE part.

        Args:
            structure: Single part BODYSTRUCTURE string.
            part_number: MIME part number.

        Returns:
            AttachmentInfo if this is an attachment, None otherwise.
        """
        # Remove outer parens
        structure = structure.strip()
        if structure.startswith("(") and structure.endswith(")"):
            structure = structure[1:-1]

        # Extract quoted strings and NIL values
        tokens = self._tokenize_bodystructure(structure)

        if len(tokens) < 2:
            return None

        # Standard BODYSTRUCTURE format:
        # (type subtype params content-id description encoding size ...)
        # For attachments, disposition info comes later

        content_type = f"{tokens[0]}/{tokens[1]}".lower()

        # Check for encryption types
        if content_type in self.ENCRYPTED_CONTENT_TYPES:
            return AttachmentInfo(
                filename="encrypted",
                content_type=content_type,
                size=0,
                content_disposition="attachment",
                part_number=part_number,
                content_id=None,
                encoding=None,
            )

        # Skip text/plain and text/html (main body, not attachments)
        if content_type in ["text/plain", "text/html"]:
            # Unless they have a filename/attachment disposition
            # Check for disposition later
            pass

        # Extract size (usually at index 6 for single parts)
        size = 0
        for i, token in enumerate(tokens):
            if isinstance(token, str) and token.isdigit():
                size = int(token)
                break

        # Look for disposition info - format: ("attachment" ("filename" "name.pdf"))
        disposition = "inline"  # Default
        filename = ""
        content_id = None
        encoding = None

        # Find encoding (usually index 5)
        if len(tokens) > 5:
            enc = tokens[5]
            if isinstance(enc, str) and enc.upper() in ["BASE64", "QUOTED-PRINTABLE", "7BIT", "8BIT"]:
                encoding = enc.upper()

        # Find content-id (usually index 3)
        if len(tokens) > 3 and tokens[3] and tokens[3] != "NIL":
            content_id = tokens[3].strip("<>")

        # Search for disposition in remaining tokens
        for i, token in enumerate(tokens):
            if isinstance(token, str) and token.upper() in ["ATTACHMENT", "INLINE"]:
                disposition = token.lower()
                # Next token might be params with filename
                if i + 1 < len(tokens):
                    params = tokens[i + 1]
                    if isinstance(params, list):
                        filename = self._extract_filename_from_params(params)
            elif isinstance(token, list):
                # Could be params or disposition params
                fname = self._extract_filename_from_params(token)
                if fname:
                    filename = fname
                    if not disposition or disposition == "inline":
                        disposition = "attachment"

        # Check params at index 2 for filename
        if not filename and len(tokens) > 2 and isinstance(tokens[2], list):
            filename = self._extract_filename_from_params(tokens[2])

        # Skip if no filename and it's a text type
        if not filename and content_type in ["text/plain", "text/html"]:
            return None

        # Generate filename from content type if needed
        if not filename:
            ext = content_type.split("/")[-1]
            filename = f"attachment.{ext}"

        return AttachmentInfo(
            filename=filename,
            content_type=content_type,
            size=size,
            content_disposition=disposition,
            part_number=part_number,
            content_id=content_id,
            encoding=encoding,
        )

    def _tokenize_bodystructure(self, structure: str) -> list[Any]:
        """Tokenize BODYSTRUCTURE string into list of values.

        Args:
            structure: BODYSTRUCTURE string.

        Returns:
            List of tokens (strings, numbers, nested lists, or NIL).
        """
        tokens: list[Any] = []
        i = 0

        while i < len(structure):
            char = structure[i]

            if char == '"':
                # Quoted string
                end = structure.find('"', i + 1)
                if end > i:
                    tokens.append(structure[i + 1 : end])
                    i = end + 1
                else:
                    i += 1

            elif char == "(":
                # Nested list
                depth = 1
                start = i + 1
                i += 1
                while i < len(structure) and depth > 0:
                    if structure[i] == "(":
                        depth += 1
                    elif structure[i] == ")":
                        depth -= 1
                    i += 1
                nested = structure[start : i - 1]
                tokens.append(self._tokenize_bodystructure(nested))

            elif char.isalnum() or char == "-":
                # Unquoted token (NIL, number, or type)
                end = i
                while end < len(structure) and (structure[end].isalnum() or structure[end] in "-_"):
                    end += 1
                token = structure[i:end]
                if token.upper() == "NIL":
                    tokens.append(None)
                else:
                    tokens.append(token)
                i = end

            else:
                i += 1

        return tokens

    def _extract_filename_from_params(self, params: list[Any]) -> str:
        """Extract filename from parameter list.

        Args:
            params: List of parameter key-value pairs.

        Returns:
            Filename if found, empty string otherwise.
        """
        if not params or not isinstance(params, list):
            return ""

        # Params are usually in pairs: ["name", "value", "name2", "value2"]
        for i in range(0, len(params) - 1, 2):
            key = params[i]
            value = params[i + 1]

            if isinstance(key, str) and key.upper() in ["NAME", "FILENAME", "FILENAME*"]:
                if isinstance(value, str):
                    # Handle RFC 2231 encoded filenames
                    if key.upper() == "FILENAME*" and "''" in value:
                        # Format: charset'language'encoded_value
                        parts = value.split("''", 1)
                        if len(parts) == 2:
                            from urllib.parse import unquote

                            return unquote(parts[1])
                    return self._decode_header(value)

        return ""
