"""Manifest management using TinyDB for tracking processed emails."""

from datetime import datetime
from pathlib import Path
from typing import Any

from tinydb import Query, TinyDB

from src.models.email import ManifestEntry


class ManifestManager:
    """Manages the processing manifest database.

    Uses TinyDB for lightweight JSON-based storage that's
    human-readable and doesn't require external services.
    """

    def __init__(self, manifest_path: Path) -> None:
        """Initialize with path to manifest database.

        Args:
            manifest_path: Path to manifest JSON file.
        """
        self.manifest_path = Path(manifest_path)
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = TinyDB(self.manifest_path)
        self._emails = self._db.table("emails")

    def record_extraction(
        self,
        email_id: str,
        imap_uid: int,
        subject: str,
        sender: str,
        date: datetime,
        labels: list[str],
        attachments: list[dict[str, Any]],
        original_size: int,
        status: str = "extracted",
    ) -> ManifestEntry:
        """Record extraction in manifest.

        Args:
            email_id: Gmail message ID (X-GM-MSGID).
            imap_uid: IMAP UID.
            subject: Email subject.
            sender: Sender address.
            date: Email date.
            labels: Gmail labels.
            attachments: List of attachment records.
            original_size: Original email size in bytes.
            status: Processing status.

        Returns:
            Created ManifestEntry.
        """
        entry = ManifestEntry(
            email_id=email_id,
            imap_uid=imap_uid,
            subject=subject,
            sender=sender,
            date=date,
            labels=labels,
            attachments=attachments,
            processed_at=datetime.now(),
            status=status,
            original_size=original_size,
        )

        # Upsert by email_id
        Email = Query()
        self._emails.upsert(entry.to_dict(), Email.email_id == email_id)

        return entry

    def get_entry(self, email_id: str) -> ManifestEntry | None:
        """Retrieve manifest entry by email ID.

        Args:
            email_id: Gmail message ID.

        Returns:
            ManifestEntry if found, None otherwise.
        """
        Email = Query()
        results = self._emails.search(Email.email_id == email_id)

        if results:
            return ManifestEntry.from_dict(results[0])
        return None

    def get_entry_by_uid(self, imap_uid: int) -> ManifestEntry | None:
        """Retrieve manifest entry by IMAP UID.

        Args:
            imap_uid: IMAP message UID.

        Returns:
            ManifestEntry if found, None otherwise.
        """
        Email = Query()
        results = self._emails.search(Email.imap_uid == imap_uid)

        if results:
            return ManifestEntry.from_dict(results[0])
        return None

    def get_entries_by_status(self, status: str) -> list[ManifestEntry]:
        """Query entries by processing status.

        Args:
            status: Status to filter by.

        Returns:
            List of matching ManifestEntry objects.
        """
        Email = Query()
        results = self._emails.search(Email.status == status)
        return [ManifestEntry.from_dict(r) for r in results]

    def update_status(
        self,
        email_id: str,
        status: str,
        stripped_size: int | None = None,
        error_message: str | None = None,
        stripped_uid: int | None = None,
        original_message_id: str | None = None,
        gmail_thread_id: str | None = None,
    ) -> bool:
        """Update processing status for an entry.

        Args:
            email_id: Gmail message ID.
            status: New status.
            stripped_size: Size after stripping (optional).
            error_message: Error message if failed (optional).
            stripped_uid: UID of stripped replacement email (optional).
            original_message_id: Message-ID header for revert (optional).
            gmail_thread_id: Thread ID for verification (optional).

        Returns:
            True if entry was updated.
        """
        Email = Query()
        updates: dict[str, Any] = {"status": status}

        if stripped_size is not None:
            updates["stripped_size"] = stripped_size
        if error_message is not None:
            updates["error_message"] = error_message
        if stripped_uid is not None:
            updates["stripped_uid"] = stripped_uid
        if original_message_id is not None:
            updates["original_message_id"] = original_message_id
        if gmail_thread_id is not None:
            updates["gmail_thread_id"] = gmail_thread_id

        result = self._emails.update(updates, Email.email_id == email_id)
        return len(result) > 0

    def get_revertible_entries(self) -> list[ManifestEntry]:
        """Get entries that can be reverted (completed with tracking info).

        Returns:
            List of ManifestEntry objects that can be reverted.
        """
        Email = Query()
        results = self._emails.search(
            (Email.status == "completed") & (Email.original_message_id.exists())
        )
        return [ManifestEntry.from_dict(r) for r in results]

    def mark_reverted(self, email_id: str, new_uid: int | None = None) -> bool:
        """Mark an entry as reverted.

        Args:
            email_id: Gmail message ID.
            new_uid: UID of the restored original email (optional).

        Returns:
            True if entry was updated.
        """
        Email = Query()
        updates: dict[str, Any] = {
            "status": "reverted",
            "reverted_at": datetime.now().isoformat(),
        }
        if new_uid is not None:
            updates["reverted_uid"] = new_uid

        result = self._emails.update(updates, Email.email_id == email_id)
        return len(result) > 0

    def is_processed(self, email_id: str) -> bool:
        """Check if email has been processed.

        Args:
            email_id: Gmail message ID.

        Returns:
            True if email exists in manifest with completed status.
        """
        entry = self.get_entry(email_id)
        return entry is not None and entry.status == "completed"

    def get_unprocessed_uids(self, all_uids: list[int]) -> list[int]:
        """Filter out already-processed UIDs.

        Args:
            all_uids: List of all UIDs to check.

        Returns:
            List of UIDs not yet processed or not completed.
        """
        # Get all completed email_ids
        completed = self.get_entries_by_status("completed")
        completed_uids = {e.imap_uid for e in completed}

        return [uid for uid in all_uids if uid not in completed_uids]

    def get_all_entries(self) -> list[ManifestEntry]:
        """Get all manifest entries.

        Returns:
            List of all ManifestEntry objects.
        """
        return [ManifestEntry.from_dict(r) for r in self._emails.all()]

    def get_processing_stats(self) -> dict[str, Any]:
        """Get summary statistics from manifest.

        Returns:
            Dictionary with processing statistics.
        """
        all_entries = self._emails.all()

        stats = {
            "total": len(all_entries),
            "by_status": {},
            "total_original_size": 0,
            "total_stripped_size": 0,
            "total_attachments": 0,
            "total_savings": 0,
        }

        for entry in all_entries:
            # Count by status
            status = entry.get("status", "unknown")
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            # Sum sizes
            stats["total_original_size"] += entry.get("original_size", 0)
            stripped = entry.get("stripped_size")
            if stripped:
                stats["total_stripped_size"] += stripped

            # Count attachments
            stats["total_attachments"] += len(entry.get("attachments", []))

        # Calculate savings
        stats["total_savings"] = stats["total_original_size"] - stats["total_stripped_size"]

        return stats

    def export_manifest(self, path: Path, format: str = "json") -> None:
        """Export manifest to JSON or CSV.

        Args:
            path: Output file path.
            format: Export format ("json" or "csv").
        """
        entries = self.get_all_entries()

        if format == "json":
            import json

            data = [e.to_dict() for e in entries]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

        elif format == "csv":
            import csv

            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                # Header
                writer.writerow(
                    [
                        "email_id",
                        "imap_uid",
                        "subject",
                        "sender",
                        "date",
                        "labels",
                        "attachment_count",
                        "processed_at",
                        "status",
                        "original_size",
                        "stripped_size",
                        "error_message",
                    ]
                )
                # Data
                for entry in entries:
                    writer.writerow(
                        [
                            entry.email_id,
                            entry.imap_uid,
                            entry.subject,
                            entry.sender,
                            entry.date.isoformat(),
                            ";".join(entry.labels),
                            len(entry.attachments),
                            entry.processed_at.isoformat(),
                            entry.status,
                            entry.original_size,
                            entry.stripped_size or "",
                            entry.error_message or "",
                        ]
                    )

    def delete_entry(self, email_id: str) -> bool:
        """Delete manifest entry.

        Args:
            email_id: Gmail message ID.

        Returns:
            True if entry was deleted.
        """
        Email = Query()
        result = self._emails.remove(Email.email_id == email_id)
        return len(result) > 0

    def clear(self) -> None:
        """Clear all manifest entries."""
        self._emails.truncate()

    def close(self) -> None:
        """Close database connection."""
        self._db.close()

    def __enter__(self) -> "ManifestManager":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()
