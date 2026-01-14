"""Attachment extraction from emails."""

import base64
import quopri
from typing import TYPE_CHECKING, Any

from src.models.email import (
    AttachmentInfo,
    EmailScanResult,
    ExtractionResult,
    SavedAttachment,
)
from src.processor.backup import BackupManager
from src.utils.logging import logger

if TYPE_CHECKING:
    from src.imap.client import GmailIMAPClient


class AttachmentExtractor:
    """Downloads and extracts attachments from emails.

    Fetches attachment content by MIME part number and saves
    to organized backup storage.
    """

    def __init__(
        self,
        client: "GmailIMAPClient",
        backup_manager: BackupManager,
    ) -> None:
        """Initialize extractor.

        Args:
            client: Connected IMAP client.
            backup_manager: Backup storage manager.
        """
        self.client = client
        self.backup_manager = backup_manager

    def extract_email(
        self,
        uid: int,
        scan_result: EmailScanResult,
        skip_inline: bool = True,
    ) -> ExtractionResult:
        """Extract all attachments from a single email.

        Args:
            uid: Message UID.
            scan_result: Scan result with attachment info.
            skip_inline: If True, skip inline attachments.

        Returns:
            ExtractionResult with saved attachments.
        """
        saved: list[SavedAttachment] = []
        errors: list[str] = []

        # Determine which attachments to extract
        attachments = scan_result.attachments
        if skip_inline:
            attachments = scan_result.strippable_attachments

        for attachment in attachments:
            try:
                saved_attachment = self._extract_attachment(
                    uid, attachment, scan_result
                )
                if saved_attachment:
                    saved.append(saved_attachment)
                    logger.debug(
                        f"Extracted: {attachment.filename} "
                        f"({attachment.size_human}) -> {saved_attachment.saved_path}"
                    )
            except Exception as e:
                error_msg = f"Failed to extract {attachment.filename}: {e}"
                errors.append(error_msg)
                logger.warning(error_msg)

        return ExtractionResult(
            uid=uid,
            success=len(errors) == 0,
            attachments_saved=saved,
            errors=errors,
        )

    def extract_batch(
        self,
        scan_results: list[EmailScanResult],
        skip_inline: bool = True,
        progress_callback: Any | None = None,
    ) -> list[ExtractionResult]:
        """Extract attachments from multiple emails.

        Args:
            scan_results: List of scan results.
            skip_inline: If True, skip inline attachments.
            progress_callback: Optional callback(current, total, message).

        Returns:
            List of ExtractionResult for each email.
        """
        results: list[ExtractionResult] = []

        for i, scan_result in enumerate(scan_results):
            if progress_callback:
                progress_callback(
                    i + 1,
                    len(scan_results),
                    f"Extracting attachments from: {scan_result.header.subject[:50]}",
                )

            result = self.extract_email(
                scan_result.header.uid,
                scan_result,
                skip_inline=skip_inline,
            )
            results.append(result)

        return results

    def fetch_attachment(self, uid: int, part_number: str) -> bytes:
        """Fetch specific MIME part by part number.

        Args:
            uid: Message UID.
            part_number: MIME part number (e.g., "2", "1.2").

        Returns:
            Raw part content (may be encoded).
        """
        return self.client.fetch_part(uid, part_number)

    def decode_attachment(self, raw_data: bytes, encoding: str | None) -> bytes:
        """Decode base64/quoted-printable content.

        Args:
            raw_data: Encoded data.
            encoding: Content-Transfer-Encoding type.

        Returns:
            Decoded bytes.
        """
        if not encoding:
            return raw_data

        encoding = encoding.upper()

        if encoding == "BASE64":
            try:
                return base64.b64decode(raw_data)
            except Exception:
                # Try cleaning up the data first
                clean_data = b"".join(raw_data.split())
                return base64.b64decode(clean_data)

        elif encoding == "QUOTED-PRINTABLE":
            return quopri.decodestring(raw_data)

        elif encoding in ("7BIT", "8BIT", "BINARY"):
            # No decoding needed
            return raw_data

        else:
            # Unknown encoding - return as-is
            logger.warning(f"Unknown encoding: {encoding}")
            return raw_data

    def _extract_attachment(
        self,
        uid: int,
        attachment: AttachmentInfo,
        scan_result: EmailScanResult,
    ) -> SavedAttachment | None:
        """Extract a single attachment.

        Args:
            uid: Message UID.
            attachment: Attachment info.
            scan_result: Full scan result for email metadata.

        Returns:
            SavedAttachment if successful, None otherwise.
        """
        # Fetch raw attachment data
        raw_data = self.fetch_attachment(uid, attachment.part_number)

        if not raw_data:
            raise ValueError(f"Empty data for part {attachment.part_number}")

        # Decode if necessary
        decoded_data = self.decode_attachment(raw_data, attachment.encoding)

        # Get backup path
        backup_path = self.backup_manager.get_backup_path(
            email_header=scan_result.header,
            filename=attachment.filename,
            labels=scan_result.gmail_metadata.labels,
        )

        # Save attachment
        saved = self.backup_manager.save_attachment(
            data=decoded_data,
            path=backup_path,
            original_filename=attachment.filename,
            content_type=attachment.content_type,
        )

        return saved


class StreamingExtractor:
    """Memory-efficient extractor for very large attachments.

    Uses chunked reading and writing to handle files larger
    than available memory.
    """

    CHUNK_SIZE = 1024 * 1024  # 1MB chunks

    def __init__(
        self,
        client: "GmailIMAPClient",
        backup_manager: BackupManager,
    ) -> None:
        """Initialize streaming extractor.

        Args:
            client: Connected IMAP client.
            backup_manager: Backup storage manager.
        """
        self.client = client
        self.backup_manager = backup_manager

    def extract_large_attachment(
        self,
        uid: int,
        attachment: AttachmentInfo,
        scan_result: EmailScanResult,
    ) -> SavedAttachment | None:
        """Extract large attachment with streaming.

        Note: Standard IMAP doesn't support true streaming,
        so this primarily helps with memory management after fetch.

        Args:
            uid: Message UID.
            attachment: Attachment info.
            scan_result: Full scan result.

        Returns:
            SavedAttachment if successful.
        """
        import hashlib
        import tempfile

        # Get backup path
        backup_path = self.backup_manager.get_backup_path(
            email_header=scan_result.header,
            filename=attachment.filename,
            labels=scan_result.gmail_metadata.labels,
        )

        # Create parent directories
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        # Fetch raw data (unfortunately IMAP requires full fetch)
        raw_data = self.client.fetch_part(uid, attachment.part_number)

        if not raw_data:
            raise ValueError(f"Empty data for part {attachment.part_number}")

        # For base64, we need to decode in chunks
        if attachment.encoding and attachment.encoding.upper() == "BASE64":
            return self._decode_base64_streaming(
                raw_data, backup_path, attachment
            )
        else:
            # Direct write with decoding if needed
            encoding = attachment.encoding
            if encoding and encoding.upper() == "QUOTED-PRINTABLE":
                decoded = quopri.decodestring(raw_data)
            else:
                decoded = raw_data

            # Write to file and compute hash
            hash_obj = hashlib.sha256()
            with open(backup_path, "wb") as f:
                for i in range(0, len(decoded), self.CHUNK_SIZE):
                    chunk = decoded[i : i + self.CHUNK_SIZE]
                    hash_obj.update(chunk)
                    f.write(chunk)

            relative_path = str(backup_path.relative_to(self.backup_manager.backup_root))

            return SavedAttachment(
                original_filename=attachment.filename,
                saved_path=relative_path,
                size=len(decoded),
                content_type=attachment.content_type,
                sha256_hash=f"sha256:{hash_obj.hexdigest()}",
            )

    def _decode_base64_streaming(
        self,
        raw_data: bytes,
        output_path: "Path",  # type: ignore
        attachment: AttachmentInfo,
    ) -> SavedAttachment:
        """Decode base64 data in chunks to file.

        Args:
            raw_data: Base64 encoded data.
            output_path: Path to write decoded data.
            attachment: Attachment info.

        Returns:
            SavedAttachment record.
        """
        import hashlib
        from pathlib import Path

        hash_obj = hashlib.sha256()
        total_size = 0

        # Clean up base64 data (remove whitespace)
        clean_data = b"".join(raw_data.split())

        with open(output_path, "wb") as f:
            # Process in chunks (must be multiple of 4 for base64)
            chunk_size = (self.CHUNK_SIZE // 3) * 4  # Adjust for base64 overhead
            chunk_size = chunk_size - (chunk_size % 4)

            for i in range(0, len(clean_data), chunk_size):
                chunk = clean_data[i : i + chunk_size]
                decoded = base64.b64decode(chunk)
                hash_obj.update(decoded)
                f.write(decoded)
                total_size += len(decoded)

        relative_path = str(output_path.relative_to(self.backup_manager.backup_root))

        return SavedAttachment(
            original_filename=attachment.filename,
            saved_path=relative_path,
            size=total_size,
            content_type=attachment.content_type,
            sha256_hash=f"sha256:{hash_obj.hexdigest()}",
        )
