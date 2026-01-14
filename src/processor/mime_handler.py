"""Low-level MIME manipulation utilities."""

from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from urllib.parse import unquote


class MIMEHandler:
    """Low-level MIME manipulation utilities.

    Provides helper methods for working with email MIME structures,
    content disposition, and encoding handling.
    """

    @staticmethod
    def is_attachment(part: EmailMessage) -> bool:
        """Check if part is an attachment (vs inline).

        Args:
            part: Email message part.

        Returns:
            True if part has attachment disposition.
        """
        disposition = part.get_content_disposition()
        return disposition == "attachment"

    @staticmethod
    def is_inline(part: EmailMessage) -> bool:
        """Check if part is inline.

        Args:
            part: Email message part.

        Returns:
            True if part has inline disposition.
        """
        disposition = part.get_content_disposition()
        return disposition == "inline"

    @staticmethod
    def is_inline_image(part: EmailMessage) -> bool:
        """Check if part is an inline image.

        These are typically referenced in HTML via cid: URLs
        and should be preserved.

        Args:
            part: Email message part.

        Returns:
            True if part is inline image.
        """
        disposition = part.get_content_disposition()
        content_type = part.get_content_type()
        return disposition == "inline" and content_type.startswith("image/")

    @staticmethod
    def has_content_id(part: EmailMessage) -> bool:
        """Check if part has a Content-ID header.

        Parts with Content-ID are typically referenced in HTML
        and should not be stripped.

        Args:
            part: Email message part.

        Returns:
            True if part has Content-ID.
        """
        return part.get("Content-ID") is not None

    @staticmethod
    def get_content_id(part: EmailMessage) -> str | None:
        """Get Content-ID header value.

        Args:
            part: Email message part.

        Returns:
            Content-ID without angle brackets, or None.
        """
        cid = part.get("Content-ID")
        if cid:
            return cid.strip("<>")
        return None

    @staticmethod
    def get_part_filename(part: EmailMessage) -> str | None:
        """Extract filename from part, handling RFC 2047/2231 encoding.

        Args:
            part: Email message part.

        Returns:
            Decoded filename or None.
        """
        # Try Content-Disposition filename parameter
        filename = part.get_filename()
        if filename:
            return MIMEHandler._decode_filename(filename)

        # Try Content-Type name parameter
        name = part.get_param("name")
        if name:
            return MIMEHandler._decode_filename(str(name))

        return None

    @staticmethod
    def _decode_filename(filename: str) -> str:
        """Decode RFC 2047/2231 encoded filename.

        Args:
            filename: Possibly encoded filename.

        Returns:
            Decoded filename.
        """
        if not filename:
            return ""

        # Handle RFC 2231 encoding (charset'language'encoded)
        if "''" in filename:
            parts = filename.split("''", 1)
            if len(parts) == 2:
                try:
                    return unquote(parts[1])
                except Exception:
                    pass

        # Handle RFC 2047 encoding (=?charset?encoding?text?=)
        if "=?" in filename:
            try:
                decoded_parts = decode_header(filename)
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
                pass

        return filename

    @staticmethod
    def get_part_size(part: EmailMessage) -> int:
        """Get approximate size of part content.

        Args:
            part: Email message part.

        Returns:
            Size in bytes (approximate for encoded content).
        """
        payload = part.get_payload(decode=False)
        if isinstance(payload, bytes):
            return len(payload)
        elif isinstance(payload, str):
            return len(payload.encode("utf-8", errors="replace"))
        return 0

    @staticmethod
    def is_multipart(msg: EmailMessage) -> bool:
        """Check if message is multipart.

        Args:
            msg: Email message.

        Returns:
            True if multipart.
        """
        return msg.is_multipart()

    @staticmethod
    def get_subtype(msg: EmailMessage) -> str:
        """Get MIME subtype (e.g., 'mixed', 'alternative').

        Args:
            msg: Email message.

        Returns:
            MIME subtype.
        """
        content_type = msg.get_content_type()
        if "/" in content_type:
            return content_type.split("/")[1]
        return ""

    @staticmethod
    def is_text_part(part: EmailMessage) -> bool:
        """Check if part is text (plain or HTML).

        Args:
            part: Email message part.

        Returns:
            True if text/plain or text/html.
        """
        content_type = part.get_content_type()
        return content_type in ("text/plain", "text/html")

    @staticmethod
    def is_encrypted(msg: EmailMessage) -> bool:
        """Check if message is encrypted (S/MIME or PGP).

        Args:
            msg: Email message.

        Returns:
            True if encrypted.
        """
        content_type = msg.get_content_type()
        encrypted_types = {
            "application/pkcs7-mime",
            "application/x-pkcs7-mime",
            "application/pgp-encrypted",
            "multipart/encrypted",
        }
        return content_type in encrypted_types


class EncodingHandler:
    """Handle various character and content encodings."""

    @staticmethod
    def decode_header_value(value: str | None) -> str:
        """Decode RFC 2047 encoded header.

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
            return str(value) if value else ""

    @staticmethod
    def encode_header_value(value: str) -> str:
        """Encode header value if needed (contains non-ASCII).

        Args:
            value: Header value to encode.

        Returns:
            Encoded string if necessary.
        """
        try:
            # Check if ASCII-encodable
            value.encode("ascii")
            return value
        except UnicodeEncodeError:
            # Needs encoding
            from email.header import Header

            return str(Header(value, "utf-8"))

    @staticmethod
    def safe_decode_payload(part: EmailMessage) -> str:
        """Safely decode part payload with fallbacks.

        Args:
            part: Email message part.

        Returns:
            Decoded string content.
        """
        payload = part.get_payload(decode=True)

        if payload is None:
            return ""

        if isinstance(payload, str):
            return payload

        # Try charset from Content-Type
        charset = part.get_content_charset()

        if charset:
            try:
                return payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                pass

        # Try common charsets
        for encoding in ["utf-8", "latin-1", "cp1252", "iso-8859-1"]:
            try:
                return payload.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue

        # Last resort
        return payload.decode("utf-8", errors="replace")

    @staticmethod
    def get_safe_charset(part: EmailMessage) -> str:
        """Get charset from part or default to utf-8.

        Args:
            part: Email message part.

        Returns:
            Charset name.
        """
        charset = part.get_content_charset()
        if charset:
            # Validate charset
            try:
                "test".encode(charset)
                return charset
            except (LookupError, UnicodeError):
                pass
        return "utf-8"
