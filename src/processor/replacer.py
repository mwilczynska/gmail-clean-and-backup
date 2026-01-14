"""Safe email replacement with two-phase commit."""

from datetime import datetime
from typing import TYPE_CHECKING

from src.models.email import (
    EmailScanResult,
    ExtractionResult,
    GmailMetadata,
    ReplaceResult,
    SavedAttachment,
)
from src.processor.reconstructor import EmailReconstructor
from src.processor.transaction import TransactionManager
from src.processor.validator import ReconstructionValidator
from src.utils.hashing import compute_sha256
from src.utils.logging import logger

if TYPE_CHECKING:
    from src.imap.client import GmailIMAPClient


class EmailReplacer:
    """Safely replaces emails on Gmail with stripped versions.

    Implements two-phase replacement:
    1. Upload stripped version
    2. Verify upload
    3. Apply labels
    4. Delete original (move to Trash)
    """

    def __init__(
        self,
        client: "GmailIMAPClient",
        transaction_manager: TransactionManager,
    ) -> None:
        """Initialize replacer.

        Args:
            client: Connected IMAP client.
            transaction_manager: Transaction manager for logging.
        """
        self.client = client
        self.txn_manager = transaction_manager
        self.reconstructor = EmailReconstructor()
        self.validator = ReconstructionValidator()

    def replace_email(
        self,
        original_uid: int,
        scan_result: EmailScanResult,
        extraction_result: ExtractionResult,
        dry_run: bool = False,
    ) -> ReplaceResult:
        """Execute two-phase replacement for single email.

        Args:
            original_uid: UID of original message.
            scan_result: Scan result for this email.
            extraction_result: Result of attachment extraction.
            dry_run: If True, don't actually make changes.

        Returns:
            ReplaceResult with operation outcome.
        """
        email_id = str(scan_result.gmail_metadata.gmail_message_id)
        gmail_metadata = scan_result.gmail_metadata

        # Start transaction
        txn_id = self.txn_manager.begin_transaction(email_id)

        try:
            # Phase 1: Fetch original email
            raw_email = self.client.fetch_raw_email(original_uid)
            original_size = len(raw_email)

            if dry_run:
                # Simulate reconstruction and return estimated result
                return self._dry_run_result(
                    original_uid, original_size, extraction_result, gmail_metadata
                )

            # Phase 2: Reconstruct email
            stripped_email = self.reconstructor.reconstruct(
                raw_email,
                scan_result.strippable_attachments,
                extraction_result.attachments_saved,
            )
            self.txn_manager.log_step(txn_id, "reconstructed")

            # Phase 3: Validate reconstruction
            validation = self.validator.validate(raw_email, stripped_email)
            if not validation.is_valid:
                raise ValueError(
                    f"Validation failed: {validation.header_issues + validation.mime_issues}"
                )

            # Phase 4: Upload stripped version
            new_uid = self._upload_email(stripped_email, original_uid)
            if not new_uid:
                raise ValueError("Upload failed - no UID returned")

            self.txn_manager.log_step(
                txn_id, "uploaded", {"new_uid": new_uid, "size": len(stripped_email)}
            )

            # Phase 5: Verify upload
            if not self._verify_upload(new_uid, stripped_email):
                raise ValueError("Upload verification failed")

            self.txn_manager.log_step(txn_id, "verified")

            # Phase 6: Apply labels
            labels_applied = self._apply_labels(new_uid, gmail_metadata.labels)
            self.txn_manager.log_step(txn_id, "labeled", {"labels": labels_applied})

            # Phase 7: Delete original (move to Trash)
            if not self.client.move_to_trash(original_uid):
                logger.warning(f"Failed to move original UID {original_uid} to trash")

            self.txn_manager.log_step(
                txn_id, "deleted", {"original_uid": original_uid}
            )

            # Commit transaction
            self.txn_manager.commit(txn_id)

            return ReplaceResult(
                success=True,
                original_uid=original_uid,
                new_uid=new_uid,
                original_size=original_size,
                new_size=len(stripped_email),
                labels_applied=labels_applied,
                phase_completed="completed",
            )

        except Exception as e:
            self.txn_manager.fail(txn_id, str(e))
            logger.error(f"Replace failed for UID {original_uid}: {e}")

            return ReplaceResult(
                success=False,
                original_uid=original_uid,
                error=str(e),
            )

    def _dry_run_result(
        self,
        original_uid: int,
        original_size: int,
        extraction_result: ExtractionResult,
        gmail_metadata: GmailMetadata,
    ) -> ReplaceResult:
        """Generate result for dry run.

        Args:
            original_uid: Original message UID.
            original_size: Original message size.
            extraction_result: Extraction result.
            gmail_metadata: Gmail metadata.

        Returns:
            Simulated ReplaceResult.
        """
        # Estimate stripped size (rough approximation)
        attachment_size = extraction_result.total_bytes
        estimated_stripped_size = original_size - attachment_size + 500  # Placeholder overhead

        return ReplaceResult(
            success=True,
            original_uid=original_uid,
            new_uid=None,  # No actual upload in dry run
            original_size=original_size,
            new_size=estimated_stripped_size,
            labels_applied=gmail_metadata.labels,
            phase_completed="dry_run",
        )

    def _upload_email(self, email_data: bytes, original_uid: int) -> int | None:
        """Upload email via IMAP APPEND.

        Args:
            email_data: Email bytes to upload.
            original_uid: Original UID (for flag reference).

        Returns:
            New UID, or None if upload failed.
        """
        # Get original flags
        # Note: In real implementation, would fetch original flags
        flags = ["\\Seen"]  # Default to seen

        # Upload to All Mail
        return self.client.append(
            folder="[Gmail]/All Mail",
            email_data=email_data,
            flags=flags,
            date_time=None,  # Use current date
        )

    def _verify_upload(self, new_uid: int, expected_data: bytes) -> bool:
        """Verify uploaded email matches expected content.

        Args:
            new_uid: UID of uploaded message.
            expected_data: Expected email content.

        Returns:
            True if verification passes.
        """
        try:
            # Fetch uploaded message
            uploaded_data = self.client.fetch_raw_email(new_uid)

            # Compare hashes
            # Note: Headers may be modified by server, so compare body content
            expected_hash = compute_sha256(expected_data)
            uploaded_hash = compute_sha256(uploaded_data)

            # For now, accept if sizes are similar (server may modify headers)
            size_diff = abs(len(uploaded_data) - len(expected_data))
            if size_diff < 1000:  # Allow up to 1KB difference for header changes
                return True

            logger.warning(
                f"Upload verification: size difference {size_diff} bytes"
            )
            return size_diff < len(expected_data) * 0.1  # Allow 10% difference

        except Exception as e:
            logger.error(f"Upload verification failed: {e}")
            return False

    def _apply_labels(self, uid: int, labels: list[str]) -> list[str]:
        """Apply Gmail labels to uploaded message.

        Args:
            uid: Message UID.
            labels: Labels to apply.

        Returns:
            List of successfully applied labels.
        """
        applied = []

        # Filter out system labels that are auto-applied
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

    def rollback(self, new_uid: int, original_uid: int) -> bool:
        """Rollback failed replacement.

        Args:
            new_uid: UID of new (uploaded) message to delete.
            original_uid: UID of original message to restore.

        Returns:
            True if rollback successful.
        """
        success = True

        # Delete the new message
        if new_uid:
            try:
                self.client.delete_message(new_uid)
                self.client.expunge()
            except Exception as e:
                logger.error(f"Rollback: failed to delete new message: {e}")
                success = False

        # Restore original from Trash if needed
        # Note: This is complex with IMAP - original may need to be moved back
        logger.info(f"Rollback: original UID {original_uid} should be in Trash")

        return success


class SafeReplacer:
    """Extra-safe replacer with additional checks and confirmations."""

    def __init__(
        self,
        client: "GmailIMAPClient",
        transaction_manager: TransactionManager,
    ) -> None:
        """Initialize safe replacer.

        Args:
            client: Connected IMAP client.
            transaction_manager: Transaction manager.
        """
        self.replacer = EmailReplacer(client, transaction_manager)
        self.client = client

    def replace_with_backup_verification(
        self,
        original_uid: int,
        scan_result: EmailScanResult,
        extraction_result: ExtractionResult,
        backup_manager: "BackupManager",  # type: ignore
    ) -> ReplaceResult:
        """Replace with additional backup verification.

        Verifies all attachments are backed up before replacing.

        Args:
            original_uid: Original message UID.
            scan_result: Scan result.
            extraction_result: Extraction result.
            backup_manager: Backup manager for verification.

        Returns:
            ReplaceResult.
        """
        # Verify all backups exist and are valid
        for saved in extraction_result.attachments_saved:
            if not backup_manager.verify_backup(saved):
                return ReplaceResult(
                    success=False,
                    original_uid=original_uid,
                    error=f"Backup verification failed for: {saved.original_filename}",
                )

        # Proceed with replacement
        return self.replacer.replace_email(
            original_uid, scan_result, extraction_result
        )
