"""Logging configuration and utilities."""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Create module logger
logger = logging.getLogger("gmail_clean")


def setup_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    verbose: bool = False,
) -> None:
    """Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional path to log file.
        verbose: If True, include debug information.
    """
    # Set level
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)

    # Clear existing handlers
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)

    # Format - simpler for console
    if verbose:
        console_format = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    else:
        console_format = logging.Formatter("%(message)s")

    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File handler if specified
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # Always verbose in file

        file_format = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)


class OperationLogger:
    """Structured logging for operations with JSONL output."""

    def __init__(self, log_path: Path | None = None) -> None:
        """Initialize operation logger.

        Args:
            log_path: Path to JSONL log file.
        """
        self.log_path = log_path
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)

    def log_operation(
        self,
        operation: str,
        email_id: str,
        success: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log operation with structured data.

        Args:
            operation: Operation name (extract, reconstruct, replace, etc.).
            email_id: Email identifier.
            success: Whether operation succeeded.
            details: Additional details.
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            "email_id": email_id,
            "success": success,
            "details": details or {},
        }

        # Log to standard logger
        if success:
            logger.info(f"{operation}: {email_id} - success")
        else:
            logger.warning(f"{operation}: {email_id} - failed")

        # Append to JSONL file
        if self.log_path:
            import json

            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    def log_error(
        self,
        operation: str,
        email_id: str,
        error: Exception,
    ) -> None:
        """Log error with stack trace.

        Args:
            operation: Operation that failed.
            email_id: Email identifier.
            error: Exception that occurred.
        """
        import traceback

        details = {
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
        }

        self.log_operation(operation, email_id, success=False, details=details)
        logger.error(f"{operation} failed for {email_id}: {error}")

    def log_batch_start(self, batch_size: int, dry_run: bool = False) -> None:
        """Log start of batch processing.

        Args:
            batch_size: Number of emails in batch.
            dry_run: Whether this is a dry run.
        """
        mode = "DRY RUN" if dry_run else "LIVE"
        logger.info(f"Starting batch processing ({mode}): {batch_size} emails")

        if self.log_path:
            import json

            entry = {
                "timestamp": datetime.now().isoformat(),
                "event": "batch_start",
                "batch_size": batch_size,
                "dry_run": dry_run,
            }
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    def log_batch_complete(
        self,
        successful: int,
        failed: int,
        skipped: int,
        bytes_saved: int,
        duration_seconds: float,
    ) -> None:
        """Log completion of batch processing.

        Args:
            successful: Number of successful operations.
            failed: Number of failed operations.
            skipped: Number of skipped emails.
            bytes_saved: Total bytes saved.
            duration_seconds: Total processing time.
        """
        logger.info(
            f"Batch complete: {successful} success, {failed} failed, "
            f"{skipped} skipped, {bytes_saved / (1024*1024):.1f}MB saved "
            f"in {duration_seconds:.1f}s"
        )

        if self.log_path:
            import json

            entry = {
                "timestamp": datetime.now().isoformat(),
                "event": "batch_complete",
                "successful": successful,
                "failed": failed,
                "skipped": skipped,
                "bytes_saved": bytes_saved,
                "duration_seconds": duration_seconds,
            }
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
