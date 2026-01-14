"""Transaction management for safe email operations."""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.utils.logging import logger

if TYPE_CHECKING:
    from src.processor.replacer import EmailReplacer


class TransactionManager:
    """Manages transactions with logging and rollback capability.

    Provides transaction tracking for multi-step operations like
    email replacement, enabling recovery from failures.
    """

    def __init__(self, log_path: Path) -> None:
        """Initialize with transaction log path.

        Args:
            log_path: Path to JSONL transaction log file.
        """
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = TransactionLog(self.log_path)

    def begin_transaction(self, email_id: str) -> str:
        """Start a new transaction.

        Args:
            email_id: Email identifier for this transaction.

        Returns:
            Transaction ID.
        """
        txn_id = str(uuid.uuid4())[:8]

        self._log.append(
            {
                "txn_id": txn_id,
                "email_id": email_id,
                "status": "started",
                "timestamp": datetime.now().isoformat(),
            }
        )

        logger.debug(f"Transaction {txn_id} started for email {email_id}")
        return txn_id

    def log_step(
        self,
        txn_id: str,
        step: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Log transaction step for recovery.

        Args:
            txn_id: Transaction ID.
            step: Step name (extracted, reconstructed, uploaded, verified, labeled, deleted).
            data: Additional data for this step.
        """
        entry = {
            "txn_id": txn_id,
            "status": step,
            "timestamp": datetime.now().isoformat(),
        }
        if data:
            entry["data"] = data

        self._log.append(entry)
        logger.debug(f"Transaction {txn_id}: {step}")

    def commit(self, txn_id: str) -> None:
        """Mark transaction as successfully completed.

        Args:
            txn_id: Transaction ID.
        """
        self._log.append(
            {
                "txn_id": txn_id,
                "status": "completed",
                "timestamp": datetime.now().isoformat(),
            }
        )
        logger.debug(f"Transaction {txn_id} committed")

    def fail(self, txn_id: str, error: str) -> None:
        """Mark transaction as failed.

        Args:
            txn_id: Transaction ID.
            error: Error message.
        """
        self._log.append(
            {
                "txn_id": txn_id,
                "status": "failed",
                "error": error,
                "timestamp": datetime.now().isoformat(),
            }
        )
        logger.warning(f"Transaction {txn_id} failed: {error}")

    def get_transaction_state(self, txn_id: str) -> dict[str, Any] | None:
        """Get current state of a transaction.

        Args:
            txn_id: Transaction ID.

        Returns:
            Dictionary with transaction state, or None if not found.
        """
        entries = self._log.read_transaction(txn_id)
        if not entries:
            return None

        # Combine all entries to get full state
        state: dict[str, Any] = {
            "txn_id": txn_id,
            "steps": [],
            "data": {},
        }

        for entry in entries:
            state["steps"].append(entry.get("status"))
            state["last_status"] = entry.get("status")
            state["last_timestamp"] = entry.get("timestamp")

            if "email_id" in entry:
                state["email_id"] = entry["email_id"]
            if "error" in entry:
                state["error"] = entry["error"]
            if "data" in entry:
                state["data"].update(entry["data"])

        return state

    def get_incomplete_transactions(self) -> list[str]:
        """Find transactions that didn't complete.

        Returns:
            List of incomplete transaction IDs.
        """
        # Read all transactions and find those without completed/failed status
        all_txns: dict[str, str] = {}

        for entry in self._log.read_all():
            txn_id = entry.get("txn_id")
            status = entry.get("status")
            if txn_id:
                all_txns[txn_id] = status

        # Filter to incomplete
        incomplete = [
            txn_id
            for txn_id, status in all_txns.items()
            if status not in ("completed", "failed")
        ]

        return incomplete

    def recover_incomplete(self, replacer: "EmailReplacer") -> int:
        """Attempt to recover/rollback incomplete transactions.

        Args:
            replacer: EmailReplacer instance for rollback operations.

        Returns:
            Number of transactions recovered.
        """
        incomplete = self.get_incomplete_transactions()
        recovered = 0

        for txn_id in incomplete:
            state = self.get_transaction_state(txn_id)
            if not state:
                continue

            try:
                self._recover_transaction(txn_id, state, replacer)
                recovered += 1
            except Exception as e:
                logger.error(f"Failed to recover transaction {txn_id}: {e}")

        return recovered

    def _recover_transaction(
        self,
        txn_id: str,
        state: dict[str, Any],
        replacer: "EmailReplacer",
    ) -> None:
        """Recover a single incomplete transaction.

        Args:
            txn_id: Transaction ID.
            state: Transaction state.
            replacer: EmailReplacer for rollback.
        """
        last_status = state.get("last_status", "")
        data = state.get("data", {})

        logger.info(f"Recovering transaction {txn_id} from state: {last_status}")

        # Determine recovery action based on last successful step
        if last_status in ("started", "extracted", "reconstructed"):
            # Nothing uploaded yet - just mark as failed
            self.fail(txn_id, "Recovered: incomplete before upload")

        elif last_status == "uploaded":
            # Uploaded but not verified - try to clean up new message
            new_uid = data.get("new_uid")
            if new_uid and replacer:
                try:
                    replacer.client.delete_message(new_uid)
                    logger.info(f"Cleaned up uploaded message UID {new_uid}")
                except Exception:
                    pass
            self.fail(txn_id, "Recovered: rolled back uploaded message")

        elif last_status in ("verified", "labeled"):
            # Almost complete - original wasn't deleted
            # This is actually recoverable - we could continue
            self.fail(txn_id, "Recovered: replacement uploaded but original not deleted")

        elif last_status == "deleted":
            # Original deleted, should be complete
            self.commit(txn_id)

    def cleanup_old_logs(self, days: int = 30) -> int:
        """Remove transaction log entries older than specified days.

        Args:
            days: Remove entries older than this many days.

        Returns:
            Number of entries removed.
        """
        cutoff = datetime.now()
        from datetime import timedelta

        cutoff = cutoff - timedelta(days=days)

        # Read all entries, filter, rewrite
        all_entries = self._log.read_all()
        kept = []
        removed = 0

        for entry in all_entries:
            timestamp_str = entry.get("timestamp", "")
            try:
                timestamp = datetime.fromisoformat(timestamp_str)
                if timestamp >= cutoff:
                    kept.append(entry)
                else:
                    removed += 1
            except Exception:
                kept.append(entry)  # Keep entries with invalid timestamps

        # Rewrite log file
        with open(self.log_path, "w", encoding="utf-8") as f:
            for entry in kept:
                f.write(json.dumps(entry) + "\n")

        return removed


class TransactionLog:
    """Append-only transaction log (JSONL format)."""

    def __init__(self, path: Path) -> None:
        """Initialize with log file path.

        Args:
            path: Path to JSONL log file.
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: dict[str, Any]) -> None:
        """Append entry to log.

        Args:
            entry: Dictionary to log.
        """
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def read_transaction(self, txn_id: str) -> list[dict[str, Any]]:
        """Read all entries for a transaction.

        Args:
            txn_id: Transaction ID.

        Returns:
            List of entries for this transaction.
        """
        entries = []

        if not self.path.exists():
            return entries

        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("txn_id") == txn_id:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue

        return entries

    def read_all(self) -> list[dict[str, Any]]:
        """Read all entries from log.

        Returns:
            List of all entries.
        """
        entries = []

        if not self.path.exists():
            return entries

        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        return entries

    def get_last_state(self, txn_id: str) -> str | None:
        """Get last recorded state of transaction.

        Args:
            txn_id: Transaction ID.

        Returns:
            Last status string or None.
        """
        entries = self.read_transaction(txn_id)
        if entries:
            return entries[-1].get("status")
        return None
