"""Backup management for extracted attachments."""

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from src.models.email import EmailHeader, SavedAttachment
from src.utils.hashing import compute_sha256


class BackupManager:
    """Manages backup storage organization and integrity.

    Handles saving attachments to organized directory structure,
    filename sanitization, and duplicate handling.
    """

    # Maximum filename length (leaving room for path)
    MAX_FILENAME_LENGTH = 100
    MAX_SUBJECT_LENGTH = 50

    # Invalid filename characters
    INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

    def __init__(
        self,
        backup_root: Path,
        organize_by: str = "date",
    ) -> None:
        """Initialize backup manager.

        Args:
            backup_root: Root directory for backups.
            organize_by: Organization strategy ("date", "sender", "label").
        """
        self.backup_root = Path(backup_root)
        self.organize_by = organize_by.lower()
        self.backup_root.mkdir(parents=True, exist_ok=True)

    def get_backup_path(
        self,
        email_header: EmailHeader,
        filename: str,
        labels: list[str] | None = None,
    ) -> Path:
        """Generate organized backup path for an attachment.

        Args:
            email_header: Email header information.
            filename: Original attachment filename.
            labels: Gmail labels (for label-based organization).

        Returns:
            Full path where attachment should be saved.
        """
        # Get organization prefix
        if self.organize_by == "sender":
            prefix = self._get_sender_path(email_header.sender)
        elif self.organize_by == "label" and labels:
            prefix = self._get_label_path(labels)
        else:
            prefix = self._get_date_path(email_header.date)

        # Sanitize subject for directory name
        subject_dir = self._sanitize_for_path(
            email_header.subject, max_length=self.MAX_SUBJECT_LENGTH
        )

        # Sanitize filename
        safe_filename = self._sanitize_filename(filename)

        # Combine path
        full_path = self.backup_root / prefix / subject_dir / safe_filename

        # Handle duplicates
        full_path = self._handle_duplicate(full_path)

        return full_path

    def save_attachment(
        self,
        data: bytes,
        path: Path,
        original_filename: str,
        content_type: str,
    ) -> SavedAttachment:
        """Save attachment data to disk.

        Args:
            data: Attachment content bytes.
            path: Full path to save to.
            original_filename: Original filename.
            content_type: MIME content type.

        Returns:
            SavedAttachment record.

        Raises:
            IOError: If save fails.
        """
        # Create parent directories
        path.parent.mkdir(parents=True, exist_ok=True)

        # Compute hash before saving
        file_hash = compute_sha256(data)

        # Write file
        with open(path, "wb") as f:
            f.write(data)

        # Get relative path for manifest
        relative_path = str(path.relative_to(self.backup_root))

        return SavedAttachment(
            original_filename=original_filename,
            saved_path=relative_path,
            size=len(data),
            content_type=content_type,
            sha256_hash=file_hash,
        )

    def verify_backup(self, saved: SavedAttachment) -> bool:
        """Verify backup integrity by re-computing hash.

        Args:
            saved: SavedAttachment record to verify.

        Returns:
            True if backup is intact.
        """
        from src.utils.hashing import verify_file_hash

        full_path = self.backup_root / saved.saved_path
        return verify_file_hash(full_path, saved.sha256_hash)

    def get_storage_stats(self) -> dict[str, Any]:
        """Calculate backup directory statistics.

        Returns:
            Dictionary with storage statistics.
        """
        total_size = 0
        file_count = 0
        by_extension: dict[str, int] = {}

        for path in self.backup_root.rglob("*"):
            if path.is_file():
                file_count += 1
                size = path.stat().st_size
                total_size += size

                ext = path.suffix.lower() or "(none)"
                by_extension[ext] = by_extension.get(ext, 0) + 1

        return {
            "total_size": total_size,
            "file_count": file_count,
            "by_extension": by_extension,
        }

    def cleanup_empty_dirs(self) -> int:
        """Remove empty directories after processing.

        Returns:
            Number of directories removed.
        """
        removed = 0

        # Walk bottom-up to remove empty dirs
        for path in sorted(self.backup_root.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    # rmdir only works on empty directories
                    path.rmdir()
                    removed += 1
                except OSError:
                    pass  # Directory not empty

        return removed

    def _get_date_path(self, date: datetime) -> Path:
        """Generate date-based path component.

        Args:
            date: Email date.

        Returns:
            Path like "2024/01/15".
        """
        return Path(f"{date.year}/{date.month:02d}/{date.day:02d}")

    def _get_sender_path(self, sender: str) -> Path:
        """Generate sender-based path component.

        Args:
            sender: Sender email address.

        Returns:
            Path like "example.com/user".
        """
        # Extract email address
        if "<" in sender and ">" in sender:
            sender = sender.split("<")[1].split(">")[0]

        sender = sender.strip().lower()

        if "@" in sender:
            local, domain = sender.rsplit("@", 1)
            # Sanitize both parts
            domain = self._sanitize_for_path(domain)
            local = self._sanitize_for_path(local, max_length=30)
            return Path(domain) / local
        else:
            return Path(self._sanitize_for_path(sender, max_length=50))

    def _get_label_path(self, labels: list[str]) -> Path:
        """Generate label-based path component.

        Args:
            labels: Gmail labels.

        Returns:
            Path based on primary label.
        """
        # Use first non-system label, or "Unlabeled"
        for label in labels:
            if not label.startswith(("INBOX", "SENT", "DRAFT", "SPAM", "TRASH")):
                return Path(self._sanitize_for_path(label, max_length=50))

        # Fall back to first label or "Unlabeled"
        if labels:
            return Path(self._sanitize_for_path(labels[0], max_length=50))
        return Path("Unlabeled")

    def _sanitize_for_path(self, text: str, max_length: int = 50) -> str:
        """Sanitize text for use in file path.

        Args:
            text: Text to sanitize.
            max_length: Maximum length.

        Returns:
            Sanitized string safe for paths.
        """
        if not text:
            return "unknown"

        # Normalize unicode
        text = unicodedata.normalize("NFKD", text)

        # Remove invalid characters
        text = self.INVALID_CHARS.sub("_", text)

        # Replace multiple underscores/spaces
        text = re.sub(r"[_\s]+", "_", text)

        # Strip leading/trailing underscores and dots
        text = text.strip("_.")

        # Truncate
        if len(text) > max_length:
            text = text[:max_length].rstrip("_.")

        # Ensure not empty
        return text if text else "unknown"

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for filesystem safety.

        Args:
            filename: Original filename.

        Returns:
            Safe filename.
        """
        if not filename:
            return "attachment"

        # Normalize unicode
        filename = unicodedata.normalize("NFKD", filename)

        # Separate name and extension
        parts = filename.rsplit(".", 1)
        name = parts[0]
        ext = f".{parts[1]}" if len(parts) > 1 else ""

        # Sanitize name
        name = self.INVALID_CHARS.sub("_", name)
        name = re.sub(r"[_\s]+", "_", name)
        name = name.strip("_.")

        # Truncate name (leaving room for extension)
        max_name_length = self.MAX_FILENAME_LENGTH - len(ext)
        if len(name) > max_name_length:
            name = name[:max_name_length].rstrip("_.")

        # Sanitize extension
        ext = self.INVALID_CHARS.sub("", ext).lower()
        if len(ext) > 10:
            ext = ext[:10]

        # Combine
        result = f"{name}{ext}" if name else f"attachment{ext}"

        return result if result else "attachment"

    def _handle_duplicate(self, path: Path) -> Path:
        """Handle filename collisions with incrementing suffix.

        Args:
            path: Desired path.

        Returns:
            Path that doesn't conflict with existing files.
        """
        if not path.exists():
            return path

        # Split into name and extension
        stem = path.stem
        suffix = path.suffix
        parent = path.parent

        # Find available name
        counter = 1
        while True:
            new_name = f"{stem}_{counter}{suffix}"
            new_path = parent / new_name
            if not new_path.exists():
                return new_path
            counter += 1
            if counter > 1000:
                # Safety limit
                raise IOError(f"Too many duplicate files: {path}")


class BackupOrganizer:
    """Alternative organization strategies for backups."""

    @staticmethod
    def by_date(email_date: datetime) -> Path:
        """Organize by year/month/day.

        Args:
            email_date: Email date.

        Returns:
            Path component.
        """
        return Path(f"{email_date.year}/{email_date.month:02d}/{email_date.day:02d}")

    @staticmethod
    def by_year_month(email_date: datetime) -> Path:
        """Organize by year/month only.

        Args:
            email_date: Email date.

        Returns:
            Path component.
        """
        return Path(f"{email_date.year}/{email_date.month:02d}")

    @staticmethod
    def by_year(email_date: datetime) -> Path:
        """Organize by year only.

        Args:
            email_date: Email date.

        Returns:
            Path component.
        """
        return Path(str(email_date.year))

    @staticmethod
    def flat() -> Path:
        """No organization - all files in root.

        Returns:
            Empty path.
        """
        return Path(".")
