"""Batch processing for email attachment stripping."""

import time
from typing import TYPE_CHECKING, Any, Callable

from src.models.email import BatchResult, EmailScanResult
from src.processor.backup import BackupManager
from src.processor.extractor import AttachmentExtractor
from src.processor.replacer import EmailReplacer
from src.processor.transaction import TransactionManager
from src.utils.logging import OperationLogger, logger
from src.utils.manifest import ManifestManager

if TYPE_CHECKING:
    from src.imap.client import GmailIMAPClient


class BatchProcessor:
    """Processes emails in batches with confirmation.

    Orchestrates the full workflow: scan -> extract -> reconstruct -> replace.
    """

    def __init__(
        self,
        client: "GmailIMAPClient",
        backup_manager: BackupManager,
        manifest_manager: ManifestManager,
        transaction_manager: TransactionManager,
        operation_logger: OperationLogger | None = None,
    ) -> None:
        """Initialize batch processor.

        Args:
            client: Connected IMAP client.
            backup_manager: Backup storage manager.
            manifest_manager: Manifest database manager.
            transaction_manager: Transaction logging manager.
            operation_logger: Optional operation logger.
        """
        self.client = client
        self.backup_manager = backup_manager
        self.manifest = manifest_manager
        self.txn_manager = transaction_manager
        self.op_logger = operation_logger or OperationLogger()

        self.extractor = AttachmentExtractor(client, backup_manager)
        self.replacer = EmailReplacer(client, transaction_manager)

    def process_batch(
        self,
        scan_results: list[EmailScanResult],
        dry_run: bool = True,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> BatchResult:
        """Process a batch of emails.

        Args:
            scan_results: List of emails to process.
            dry_run: If True, show what would happen without making changes.
            progress_callback: Optional callback(current, total, message).

        Returns:
            BatchResult with processing statistics.
        """
        if dry_run:
            self.op_logger.log_batch_start(len(scan_results), dry_run=True)
            return self._dry_run_batch(scan_results, progress_callback)
        else:
            self.op_logger.log_batch_start(len(scan_results), dry_run=False)
            return self._execute_batch(scan_results, progress_callback)

    def _dry_run_batch(
        self,
        scan_results: list[EmailScanResult],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> BatchResult:
        """Execute dry run - show what would be processed.

        Args:
            scan_results: Emails to analyze.
            progress_callback: Progress callback.

        Returns:
            Simulated BatchResult.
        """
        successful = 0
        skipped = 0
        total_savings = 0

        for i, scan_result in enumerate(scan_results):
            if progress_callback:
                progress_callback(
                    i + 1,
                    len(scan_results),
                    f"[DRY RUN] Analyzing: {scan_result.header.subject[:40]}...",
                )

            # Check if processable
            if not scan_result.can_process:
                skipped += 1
                continue

            # Check if already processed
            email_id = str(scan_result.gmail_metadata.gmail_message_id)
            if self.manifest.is_processed(email_id):
                skipped += 1
                continue

            # Estimate savings
            total_savings += scan_result.strippable_size
            successful += 1

        return BatchResult(
            total_processed=len(scan_results),
            successful=successful,
            failed=0,
            skipped=skipped,
            total_bytes_saved=total_savings,
        )

    def _execute_batch(
        self,
        scan_results: list[EmailScanResult],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> BatchResult:
        """Execute actual batch processing.

        Args:
            scan_results: Emails to process.
            progress_callback: Progress callback.

        Returns:
            BatchResult with actual results.
        """
        start_time = time.time()

        successful = 0
        failed = 0
        skipped = 0
        total_bytes_saved = 0
        errors: list[dict[str, Any]] = []

        # Calculate total steps: each email has 4 steps (extract, backup, reconstruct, upload)
        # Plus 1 step for skipped emails (just checking)
        total_steps = len(scan_results) * 4
        current_step = 0

        for i, scan_result in enumerate(scan_results):
            email_id = str(scan_result.gmail_metadata.gmail_message_id)
            uid = scan_result.header.uid
            subject = scan_result.header.subject[:30]

            # Check if processable
            if not scan_result.can_process:
                skipped += 1
                current_step += 4  # Skip all steps for this email
                if progress_callback:
                    progress_callback(current_step, total_steps, f"Skipped: {subject}...")
                logger.debug(f"Skipped UID {uid}: not processable")
                continue

            # Check if already processed
            if self.manifest.is_processed(email_id):
                skipped += 1
                current_step += 4  # Skip all steps for this email
                if progress_callback:
                    progress_callback(current_step, total_steps, f"Already done: {subject}...")
                logger.debug(f"Skipped UID {uid}: already processed")
                continue

            try:
                # Process single email with step-by-step progress
                result = self._process_single_with_progress(
                    scan_result,
                    progress_callback,
                    current_step,
                    total_steps,
                )
                current_step += 4  # All 4 steps completed

                if result["success"]:
                    successful += 1
                    total_bytes_saved += result.get("bytes_saved", 0)
                    self.op_logger.log_operation(
                        "process", email_id, True, result
                    )
                else:
                    failed += 1
                    errors.append({"email_id": email_id, "error": result.get("error")})
                    self.op_logger.log_operation(
                        "process", email_id, False, result
                    )

            except Exception as e:
                failed += 1
                current_step += 4  # Move past this email's steps
                errors.append({"email_id": email_id, "error": str(e)})
                self.op_logger.log_error("process", email_id, e)

        duration = time.time() - start_time

        self.op_logger.log_batch_complete(
            successful, failed, skipped, total_bytes_saved, duration
        )

        return BatchResult(
            total_processed=len(scan_results),
            successful=successful,
            failed=failed,
            skipped=skipped,
            total_bytes_saved=total_bytes_saved,
            errors=errors,
            duration_seconds=duration,
        )

    def _process_single(self, scan_result: EmailScanResult) -> dict[str, Any]:
        """Process a single email.

        Args:
            scan_result: Email scan result.

        Returns:
            Dictionary with processing result.
        """
        return self._process_single_with_progress(scan_result, None, 0, 1)

    def _process_single_with_progress(
        self,
        scan_result: EmailScanResult,
        progress_callback: Callable[[int, int, str], None] | None,
        base_step: int,
        total_steps: int,
    ) -> dict[str, Any]:
        """Process a single email with step-by-step progress updates.

        Args:
            scan_result: Email scan result.
            progress_callback: Optional callback(current, total, message).
            base_step: Starting step number for this email.
            total_steps: Total steps in the batch.

        Returns:
            Dictionary with processing result.
        """
        uid = scan_result.header.uid
        email_id = str(scan_result.gmail_metadata.gmail_message_id)
        subject = scan_result.header.subject[:25]

        # Step 1: Extract attachments
        if progress_callback:
            progress_callback(base_step + 1, total_steps, f"Extracting: {subject}...")

        extraction = self.extractor.extract_email(uid, scan_result)

        if not extraction.success:
            return {
                "success": False,
                "error": f"Extraction failed: {extraction.errors}",
            }

        # Step 2: Record in manifest (backup tracking)
        if progress_callback:
            progress_callback(base_step + 2, total_steps, f"Backing up: {subject}...")

        self.manifest.record_extraction(
            email_id=email_id,
            imap_uid=uid,
            subject=scan_result.header.subject,
            sender=scan_result.header.sender,
            date=scan_result.header.date,
            labels=scan_result.gmail_metadata.labels,
            attachments=[a.to_dict() for a in extraction.attachments_saved],
            original_size=scan_result.header.size,
            status="extracted",
        )

        # Step 3: Reconstruct and upload email without attachments
        # (This is where the heavy work happens - MIME reconstruction + IMAP upload)
        if progress_callback:
            progress_callback(base_step + 3, total_steps, f"Rebuilding: {subject}...")

        replace_result = self.replacer.replace_email(
            uid, scan_result, extraction, dry_run=False
        )

        # Step 4: Finalize (verify upload, move original to trash)
        if progress_callback:
            progress_callback(base_step + 4, total_steps, f"Finalizing: {subject}...")

        if not replace_result.success:
            self.manifest.update_status(
                email_id, "failed", error_message=replace_result.error
            )
            return {
                "success": False,
                "error": replace_result.error,
            }

        # Update manifest with revert tracking info
        self.manifest.update_status(
            email_id,
            "completed",
            stripped_size=replace_result.new_size,
            stripped_uid=replace_result.new_uid,
            original_message_id=scan_result.header.message_id,
            gmail_thread_id=str(scan_result.gmail_metadata.gmail_thread_id),
        )

        return {
            "success": True,
            "original_size": replace_result.original_size,
            "new_size": replace_result.new_size,
            "bytes_saved": replace_result.size_saved,
            "new_uid": replace_result.new_uid,
        }


class BatchPreview:
    """Generate preview of what batch processing will do."""

    def __init__(self, scan_results: list[EmailScanResult]) -> None:
        """Initialize with scan results.

        Args:
            scan_results: Emails to preview.
        """
        self.scan_results = scan_results

    def generate_summary(self) -> dict[str, Any]:
        """Generate summary of batch operation.

        Returns:
            Dictionary with summary statistics.
        """
        processable = [r for r in self.scan_results if r.can_process]
        encrypted = [r for r in self.scan_results if r.is_encrypted]
        inline_only = [
            r for r in self.scan_results if r.attachments and not r.strippable_attachments
        ]

        total_size = sum(r.strippable_size for r in processable)

        return {
            "total_emails": len(self.scan_results),
            "processable": len(processable),
            "encrypted_skipped": len(encrypted),
            "inline_only_skipped": len(inline_only),
            "total_attachments": sum(len(r.strippable_attachments) for r in processable),
            "estimated_savings_bytes": total_size,
            "estimated_savings_human": self._format_size(total_size),
        }

    def _format_size(self, size: int) -> str:
        """Format size in human-readable form."""
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.2f} GB"

    def get_by_year(self) -> dict[int, int]:
        """Group processable emails by year.

        Returns:
            Dictionary mapping year to email count.
        """
        by_year: dict[int, int] = {}
        for result in self.scan_results:
            if result.can_process:
                year = result.header.date.year
                by_year[year] = by_year.get(year, 0) + 1
        return dict(sorted(by_year.items()))

    def get_by_sender(self, top_n: int = 10) -> list[tuple[str, int]]:
        """Get top senders with most attachments.

        Args:
            top_n: Number of top senders to return.

        Returns:
            List of (sender, count) tuples.
        """
        by_sender: dict[str, int] = {}
        for result in self.scan_results:
            if result.can_process:
                sender = result.header.sender
                by_sender[sender] = by_sender.get(sender, 0) + 1

        sorted_senders = sorted(by_sender.items(), key=lambda x: -x[1])
        return sorted_senders[:top_n]


class CheckpointManager:
    """Manage processing checkpoints for resume capability."""

    def __init__(self, checkpoint_path: "Path") -> None:  # type: ignore
        """Initialize checkpoint manager.

        Args:
            checkpoint_path: Path to checkpoint file.
        """
        from pathlib import Path

        self.checkpoint_path = Path(checkpoint_path)
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    def save_checkpoint(
        self,
        last_processed_uid: int,
        stats: dict[str, Any],
    ) -> None:
        """Save processing checkpoint.

        Args:
            last_processed_uid: Last successfully processed UID.
            stats: Current statistics.
        """
        import json
        from datetime import datetime

        checkpoint = {
            "last_processed_uid": last_processed_uid,
            "timestamp": datetime.now().isoformat(),
            "stats": stats,
        }

        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f, indent=2)

    def load_checkpoint(self) -> dict[str, Any] | None:
        """Load last checkpoint.

        Returns:
            Checkpoint data or None if not found.
        """
        import json

        if not self.checkpoint_path.exists():
            return None

        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def clear_checkpoint(self) -> None:
        """Remove checkpoint file."""
        if self.checkpoint_path.exists():
            self.checkpoint_path.unlink()
