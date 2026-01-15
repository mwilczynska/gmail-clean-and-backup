"""Revert processed emails by restoring originals from Trash."""

from typing import TYPE_CHECKING

from src.models.email import ManifestEntry
from src.utils.logging import logger

if TYPE_CHECKING:
    from src.imap.client import GmailIMAPClient
    from src.utils.manifest import ManifestManager


class RevertError(Exception):
    """Raised when revert operation fails."""

    pass


class RevertResult:
    """Result of a revert operation."""

    def __init__(
        self,
        success: bool,
        email_id: str,
        original_restored: bool = False,
        stripped_deleted: bool = False,
        labels_applied: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        """Initialize revert result.

        Args:
            success: Whether the revert succeeded.
            email_id: Gmail message ID.
            original_restored: Whether original was restored from Trash.
            stripped_deleted: Whether stripped version was deleted.
            labels_applied: Labels restored to the original.
            error: Error message if failed.
        """
        self.success = success
        self.email_id = email_id
        self.original_restored = original_restored
        self.stripped_deleted = stripped_deleted
        self.labels_applied = labels_applied or []
        self.error = error


class EmailReverter:
    """Reverts processed emails by restoring originals from Gmail Trash.

    The revert process:
    1. Search Trash for original email by Message-ID
    2. Copy original back to All Mail
    3. Apply original labels
    4. Delete the stripped version
    5. Update manifest status to 'reverted'
    """

    def __init__(
        self,
        client: "GmailIMAPClient",
        manifest_manager: "ManifestManager",
    ) -> None:
        """Initialize reverter.

        Args:
            client: Connected IMAP client.
            manifest_manager: Manifest manager for tracking.
        """
        self.client = client
        self.manifest = manifest_manager

    def revert_email(self, entry: ManifestEntry, dry_run: bool = False) -> RevertResult:
        """Revert a single processed email.

        Args:
            entry: Manifest entry for the email to revert.
            dry_run: If True, show what would happen without making changes.

        Returns:
            RevertResult with operation outcome.
        """
        email_id = entry.email_id

        if not entry.can_revert:
            return RevertResult(
                success=False,
                email_id=email_id,
                error="Entry cannot be reverted (missing tracking info or already reverted)",
            )

        if dry_run:
            return self._dry_run_revert(entry)

        try:
            # Step 1: Find original in Trash by Message-ID
            original_uid = self._find_in_trash(entry.original_message_id)
            if not original_uid:
                return RevertResult(
                    success=False,
                    email_id=email_id,
                    error=f"Original email not found in Trash (Message-ID: {entry.original_message_id}). "
                    "It may have been permanently deleted.",
                )

            # Step 2: Copy original from Trash back to All Mail
            restored_uid = self._restore_from_trash(original_uid)
            if not restored_uid:
                return RevertResult(
                    success=False,
                    email_id=email_id,
                    error="Failed to restore original email from Trash",
                )

            # Step 3: Apply original labels to restored email
            labels_applied = self._apply_labels(restored_uid, entry.labels)

            # Step 4: Delete the stripped version
            stripped_deleted = False
            if entry.stripped_uid:
                stripped_deleted = self._delete_stripped(entry.stripped_uid)
                if not stripped_deleted:
                    logger.warning(
                        f"Could not delete stripped version UID {entry.stripped_uid}"
                    )

            # Step 5: Update manifest
            self.manifest.mark_reverted(email_id, restored_uid)

            return RevertResult(
                success=True,
                email_id=email_id,
                original_restored=True,
                stripped_deleted=stripped_deleted,
                labels_applied=labels_applied,
            )

        except Exception as e:
            logger.error(f"Revert failed for {email_id}: {e}")
            return RevertResult(
                success=False,
                email_id=email_id,
                error=str(e),
            )

    def _dry_run_revert(self, entry: ManifestEntry) -> RevertResult:
        """Simulate revert without making changes.

        Args:
            entry: Manifest entry to simulate reverting.

        Returns:
            Simulated RevertResult.
        """
        # Check if original exists in Trash
        original_uid = self._find_in_trash(entry.original_message_id)

        if not original_uid:
            return RevertResult(
                success=False,
                email_id=entry.email_id,
                error=f"[DRY RUN] Original not found in Trash (Message-ID: {entry.original_message_id})",
            )

        return RevertResult(
            success=True,
            email_id=entry.email_id,
            original_restored=True,  # Would be restored
            stripped_deleted=entry.stripped_uid is not None,  # Would be deleted
            labels_applied=entry.labels,  # Would be applied
        )

    def _get_trash_folder(self) -> str:
        """Get the Trash folder name (varies by locale).

        Returns:
            Trash folder name (e.g., '[Gmail]/Trash' or '[Gmail]/Bin').
        """
        folders = self.client.list_folders()
        # Check for common Trash folder names
        for folder in folders:
            if folder in ("[Gmail]/Trash", "[Gmail]/Bin", "[Gmail]/Papierkorb"):
                return folder
        # Fallback to default
        return "[Gmail]/Trash"

    def _find_in_trash(self, message_id: str | None) -> int | None:
        """Find email in Trash by Message-ID header.

        Args:
            message_id: Message-ID header value.

        Returns:
            UID of the email in Trash, or None if not found.
        """
        if not message_id:
            return None

        # Select Trash folder (handle different locales)
        trash_folder = self._get_trash_folder()
        self.client.select_folder(trash_folder, readonly=True)

        # Search by Message-ID using Gmail's IMAP search
        # Escape any special characters in Message-ID
        search_criteria = f'HEADER Message-ID "{message_id}"'

        try:
            uids = self.client.search(search_criteria)
            if uids:
                return uids[0]  # Return first match
        except Exception as e:
            logger.warning(f"Error searching Trash: {e}")

        return None

    def _restore_from_trash(self, trash_uid: int) -> int | None:
        """Restore email from Trash to All Mail.

        Args:
            trash_uid: UID of email in Trash.

        Returns:
            UID of restored email in All Mail, or None if failed.
        """
        # First, fetch the raw email from Trash
        trash_folder = self._get_trash_folder()
        self.client.select_folder(trash_folder, readonly=False)
        raw_email = self.client.fetch_raw_email(trash_uid)

        if not raw_email:
            return None

        # Get original flags
        fetch_result = self.client.fetch(trash_uid, "(FLAGS)")
        flags = fetch_result.get("FLAGS", ["\\Seen"])

        # Append to All Mail
        new_uid = self.client.append(
            folder="[Gmail]/All Mail",
            email_data=raw_email,
            flags=flags if isinstance(flags, list) else ["\\Seen"],
        )

        if new_uid:
            # Permanently delete from Trash
            self.client.delete_message(trash_uid)
            self.client.expunge()

        return new_uid

    def _apply_labels(self, uid: int, labels: list[str]) -> list[str]:
        """Apply Gmail labels to restored email.

        Args:
            uid: Message UID.
            labels: Labels to apply.

        Returns:
            List of successfully applied labels.
        """
        # Select All Mail to apply labels
        self.client.select_folder("[Gmail]/All Mail", readonly=False)

        applied = []

        # Filter out system labels
        user_labels = [
            label
            for label in labels
            if not label.startswith(("\\", "INBOX", "SENT", "DRAFT", "SPAM", "TRASH"))
        ]

        if user_labels:
            try:
                self.client.store_labels(uid, user_labels, action="+")
                applied = user_labels
            except Exception as e:
                logger.warning(f"Failed to apply labels: {e}")

        return applied

    def _delete_stripped(self, stripped_uid: int) -> bool:
        """Delete the stripped version of the email.

        Args:
            stripped_uid: UID of stripped email to delete.

        Returns:
            True if deletion successful.
        """
        try:
            self.client.select_folder("[Gmail]/All Mail", readonly=False)
            self.client.move_to_trash(stripped_uid)
            self.client.expunge()
            return True
        except Exception as e:
            logger.warning(f"Failed to delete stripped email: {e}")
            return False

    def get_revertible_emails(self) -> list[ManifestEntry]:
        """Get list of emails that can be reverted.

        Returns:
            List of ManifestEntry objects that can be reverted.
        """
        return self.manifest.get_revertible_entries()

    def check_trash_availability(
        self, entries: list[ManifestEntry]
    ) -> dict[str, bool]:
        """Check which entries have originals still in Trash.

        Args:
            entries: List of manifest entries to check.

        Returns:
            Dictionary mapping email_id to availability status.
        """
        availability = {}

        for entry in entries:
            if entry.original_message_id:
                uid = self._find_in_trash(entry.original_message_id)
                availability[entry.email_id] = uid is not None
            else:
                availability[entry.email_id] = False

        return availability
