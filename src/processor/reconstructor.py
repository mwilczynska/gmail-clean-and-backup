"""Email reconstruction with attachments stripped."""

from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from src.models.email import AttachmentInfo, SavedAttachment
from src.processor.mime_handler import EncodingHandler, MIMEHandler
from src.utils.logging import logger


class EmailReconstructor:
    """Reconstructs emails with attachments stripped.

    Preserves all headers (especially threading-related) and
    replaces attachment parts with placeholder text.
    """

    # Headers critical for threading - must be preserved exactly
    CRITICAL_HEADERS = [
        "Message-ID",
        "Date",
        "From",
        "To",
        "Cc",
        "Bcc",
        "Subject",
        "In-Reply-To",
        "References",
        "Reply-To",
        "MIME-Version",
        "Content-Type",
    ]

    # Headers to preserve but not critical
    PRESERVE_HEADERS = [
        "Received",
        "Return-Path",
        "X-Mailer",
        "User-Agent",
        "Thread-Index",
        "Thread-Topic",
        "X-Priority",
        "Importance",
        "X-MS-Has-Attach",
        "X-MS-TNEF-Correlator",
    ]

    def __init__(
        self,
        preserve_inline: bool = True,
        placeholder_template: str | None = None,
    ) -> None:
        """Initialize reconstructor.

        Args:
            preserve_inline: If True, keep inline images.
            placeholder_template: Custom template for placeholder text.
        """
        self.preserve_inline = preserve_inline
        self.placeholder_template = placeholder_template or self._default_placeholder_template()
        self._parser = BytesParser(policy=policy.default)

    def _default_placeholder_template(self) -> str:
        """Default placeholder template for removed attachments."""
        return """
[Attachment Removed]
Filename: {filename}
Original Size: {size_human}
Content Type: {content_type}
Backup Location: {backup_path}
Processed: {processed_at}
Tool: gmail-clean-and-backup
""".strip()

    def reconstruct(
        self,
        raw_email: bytes,
        attachments_to_strip: list[AttachmentInfo],
        saved_attachments: list[SavedAttachment],
    ) -> bytes:
        """Main reconstruction entry point.

        Args:
            raw_email: Original raw email bytes.
            attachments_to_strip: List of attachments to remove.
            saved_attachments: List of saved attachment records.

        Returns:
            Reconstructed email bytes.
        """
        # Parse original email
        original = self.parse_email(raw_email)

        # Save the original Content-Type for recovery if needed
        original_content_type = original.get("Content-Type")

        # Create mapping of filename to backup path
        backup_paths = {
            saved.original_filename: saved.saved_path for saved in saved_attachments
        }

        # Strip attachments
        reconstructed = self._strip_attachments(
            original, attachments_to_strip, backup_paths
        )

        # Ensure Content-Type header exists before serialization
        # Python's email library can lose Content-Type during set_payload()
        if not reconstructed.get("Content-Type"):
            logger.debug("Content-Type missing after reconstruction, recovering")
            if original_content_type:
                # Restore original Content-Type
                reconstructed["Content-Type"] = original_content_type
            elif reconstructed.is_multipart():
                # Generate a Content-Type for multipart
                boundary = reconstructed.get_boundary() or "===============boundary==============="
                reconstructed["Content-Type"] = f"multipart/mixed; boundary=\"{boundary}\""
            else:
                # Default to text/plain
                reconstructed["Content-Type"] = "text/plain; charset=utf-8"

        # Serialize back to bytes
        return self.serialize(reconstructed)

    def parse_email(self, raw_email: bytes) -> EmailMessage:
        """Parse raw email into EmailMessage object.

        Args:
            raw_email: Raw email bytes.

        Returns:
            Parsed EmailMessage.
        """
        return self._parser.parsebytes(raw_email)

    def serialize(self, msg: EmailMessage) -> bytes:
        """Serialize EmailMessage back to bytes.

        Args:
            msg: Email message to serialize.

        Returns:
            Raw email bytes.
        """
        return msg.as_bytes(policy=policy.SMTP)

    def _strip_attachments(
        self,
        msg: EmailMessage,
        attachments: list[AttachmentInfo],
        backup_paths: dict[str, str],
    ) -> EmailMessage:
        """Strip specified attachments from message.

        Args:
            msg: Email message.
            attachments: Attachments to strip.
            backup_paths: Map of filename to backup path.

        Returns:
            Modified message.
        """
        # Create set of filenames to strip for fast lookup
        filenames_to_strip = {att.filename for att in attachments}

        if not msg.is_multipart():
            # Simple message - check if it's an attachment
            filename = MIMEHandler.get_part_filename(msg)
            if filename and filename in filenames_to_strip:
                # Replace entire message content with placeholder
                placeholder = self._create_placeholder(
                    filename,
                    attachments[0] if attachments else None,
                    backup_paths.get(filename, ""),
                )
                msg.set_content(placeholder, subtype="plain", charset="utf-8")
            return msg

        # Multipart message - process recursively
        return self._process_multipart(msg, filenames_to_strip, backup_paths, attachments)

    def _process_multipart(
        self,
        msg: EmailMessage,
        filenames_to_strip: set[str],
        backup_paths: dict[str, str],
        attachments: list[AttachmentInfo],
    ) -> EmailMessage:
        """Process multipart message, stripping attachments.

        Args:
            msg: Multipart message.
            filenames_to_strip: Set of filenames to strip.
            backup_paths: Map of filename to backup path.
            attachments: Full attachment info list.

        Returns:
            Modified message.
        """
        # Get attachment info lookup
        attachment_info = {att.filename: att for att in attachments}

        # Preserve the original Content-Type header before modifying payload
        # set_payload() can clear or corrupt the Content-Type header
        original_content_type = msg.get("Content-Type")
        original_boundary = msg.get_boundary()

        # Build new payload list
        new_payload = []
        placeholders_added = []

        for part in msg.iter_parts():
            # Check if this part should be stripped
            filename = MIMEHandler.get_part_filename(part)

            if filename and filename in filenames_to_strip:
                # Check if we should preserve inline
                if self.preserve_inline and MIMEHandler.has_content_id(part):
                    # Keep inline attachment
                    new_payload.append(part)
                    continue

                # Create placeholder part
                att_info = attachment_info.get(filename)
                placeholder_text = self._create_placeholder(
                    filename,
                    att_info,
                    backup_paths.get(filename, ""),
                )
                placeholders_added.append(placeholder_text)
                continue

            # Check for nested multipart
            if part.is_multipart():
                # Recursively process
                processed = self._process_multipart(
                    part, filenames_to_strip, backup_paths, attachments
                )
                new_payload.append(processed)
            else:
                # Keep this part
                new_payload.append(part)

        # If we removed attachments, add a single combined placeholder
        if placeholders_added:
            # Add placeholder as text/plain part
            combined_placeholder = "\n\n---\n\n".join(placeholders_added)
            placeholder_part = self._create_placeholder_part(combined_placeholder)

            # For multipart/mixed, add placeholder at the end
            # For multipart/alternative, we need to handle differently
            subtype = MIMEHandler.get_subtype(msg)

            if subtype == "alternative":
                # Insert before last part (usually HTML)
                if new_payload:
                    new_payload.insert(-1, placeholder_part)
                else:
                    new_payload.append(placeholder_part)
            else:
                new_payload.append(placeholder_part)

        # Replace message payload
        msg.set_payload(new_payload)

        # Restore the Content-Type header if it was lost or corrupted
        # Python's email library can lose Content-Type during set_payload()
        if original_content_type and not msg.get("Content-Type"):
            msg["Content-Type"] = original_content_type
        elif original_boundary and msg.get_boundary() != original_boundary:
            # Boundary was corrupted, restore it
            msg.set_boundary(original_boundary)

        return msg

    def _create_placeholder(
        self,
        filename: str,
        attachment_info: AttachmentInfo | None,
        backup_path: str,
    ) -> str:
        """Create placeholder text for removed attachment.

        Args:
            filename: Original filename.
            attachment_info: Full attachment info.
            backup_path: Path where attachment was saved.

        Returns:
            Placeholder text.
        """
        size_human = "Unknown"
        content_type = "Unknown"

        if attachment_info:
            size_human = attachment_info.size_human
            content_type = attachment_info.content_type

        return self.placeholder_template.format(
            filename=filename,
            size_human=size_human,
            content_type=content_type,
            backup_path=backup_path,
            processed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    def _create_placeholder_part(self, text: str) -> EmailMessage:
        """Create a text/plain part for placeholder.

        Args:
            text: Placeholder text.

        Returns:
            EmailMessage part.
        """
        part = EmailMessage(policy=policy.default)
        part.set_content(text, subtype="plain", charset="utf-8")
        return part


class MIMETreeWalker:
    """Recursively process MIME tree structure."""

    MAX_DEPTH = 20  # Prevent infinite recursion

    @staticmethod
    def walk_and_modify(
        msg: EmailMessage,
        modifier: Any,
        depth: int = 0,
    ) -> EmailMessage:
        """Walk MIME tree, applying modifier function to each part.

        Args:
            msg: Email message.
            modifier: Callable that takes (part, depth) and returns modified part or None.
            depth: Current recursion depth.

        Returns:
            Modified message.
        """
        if depth > MIMETreeWalker.MAX_DEPTH:
            logger.warning(f"MIME tree depth exceeded {MIMETreeWalker.MAX_DEPTH}")
            return msg

        if not msg.is_multipart():
            # Leaf node - apply modifier
            result = modifier(msg, depth)
            return result if result is not None else msg

        # Preserve Content-Type header before modifying payload
        original_content_type = msg.get("Content-Type")
        original_boundary = msg.get_boundary()

        # Process each part
        new_parts = []
        for part in msg.iter_parts():
            modified_part = MIMETreeWalker.walk_and_modify(part, modifier, depth + 1)
            if modified_part is not None:
                new_parts.append(modified_part)

        # Update message with modified parts
        if new_parts:
            msg.set_payload(new_parts)

        # Restore Content-Type header if lost
        if original_content_type and not msg.get("Content-Type"):
            msg["Content-Type"] = original_content_type
        elif original_boundary and msg.get_boundary() != original_boundary:
            msg.set_boundary(original_boundary)

        return msg

    @staticmethod
    def get_depth(msg: EmailMessage) -> int:
        """Get maximum depth of MIME tree.

        Args:
            msg: Email message.

        Returns:
            Maximum nesting depth.
        """
        if not msg.is_multipart():
            return 1

        max_child_depth = 0
        for part in msg.iter_parts():
            child_depth = MIMETreeWalker.get_depth(part)
            max_child_depth = max(max_child_depth, child_depth)

        return 1 + max_child_depth

    @staticmethod
    def count_parts(msg: EmailMessage) -> int:
        """Count total parts in MIME tree.

        Args:
            msg: Email message.

        Returns:
            Total part count.
        """
        if not msg.is_multipart():
            return 1

        count = 1
        for part in msg.iter_parts():
            count += MIMETreeWalker.count_parts(part)

        return count

    @staticmethod
    def find_text_parts(msg: EmailMessage) -> list[EmailMessage]:
        """Find all text/plain and text/html parts.

        Args:
            msg: Email message.

        Returns:
            List of text parts.
        """
        text_parts = []

        if not msg.is_multipart():
            if MIMEHandler.is_text_part(msg):
                text_parts.append(msg)
            return text_parts

        for part in msg.iter_parts():
            text_parts.extend(MIMETreeWalker.find_text_parts(part))

        return text_parts


class SimpleReconstructor:
    """Simplified reconstructor for common cases.

    Handles the most common email structures without
    full MIME tree manipulation.
    """

    def __init__(self, preserve_inline: bool = True) -> None:
        """Initialize simple reconstructor.

        Args:
            preserve_inline: If True, preserve inline attachments.
        """
        self.preserve_inline = preserve_inline
        self._parser = BytesParser(policy=policy.default)

    def reconstruct_simple(
        self,
        raw_email: bytes,
        attachments_removed: list[dict[str, Any]],
    ) -> bytes:
        """Simple reconstruction by appending notice to body.

        Instead of modifying MIME structure, this just appends
        a notice about removed attachments to the email body.

        Args:
            raw_email: Original email bytes.
            attachments_removed: List of attachment info dicts.

        Returns:
            Modified email bytes.
        """
        msg = self._parser.parsebytes(raw_email)

        # Build notice text
        notice_lines = ["\n\n--- Attachments Removed ---\n"]
        for att in attachments_removed:
            notice_lines.append(
                f"- {att['filename']} ({att.get('size_human', 'Unknown size')}) "
                f"-> {att.get('backup_path', 'backup location')}"
            )
        notice_lines.append("\nBackup created by gmail-clean-and-backup")
        notice = "\n".join(notice_lines)

        # Find and modify text parts
        self._append_to_text_parts(msg, notice)

        return msg.as_bytes(policy=policy.SMTP)

    def _append_to_text_parts(self, msg: EmailMessage, notice: str) -> None:
        """Append notice to text parts of message.

        Args:
            msg: Email message.
            notice: Text to append.
        """
        if not msg.is_multipart():
            if msg.get_content_type() == "text/plain":
                content = EncodingHandler.safe_decode_payload(msg)
                msg.set_content(content + notice, subtype="plain", charset="utf-8")
            return

        for part in msg.iter_parts():
            self._append_to_text_parts(part, notice)
