"""Data models for email processing."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class EmailHeader:
    """Parsed email header information."""

    uid: int
    message_id: str
    subject: str
    sender: str
    recipients: list[str]
    date: datetime
    size: int
    has_attachments: bool

    # Optional headers for threading
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        """Human-readable representation."""
        date_str = self.date.strftime("%Y-%m-%d %H:%M")
        size_kb = self.size / 1024
        return f"[{date_str}] {self.sender}: {self.subject} ({size_kb:.1f}KB)"


@dataclass
class GmailMetadata:
    """Gmail-specific metadata from IMAP extensions."""

    gmail_message_id: int  # X-GM-MSGID - unique message identifier
    gmail_thread_id: int  # X-GM-THRID - conversation thread ID
    labels: list[str]  # X-GM-LABELS - Gmail labels/folders

    def has_label(self, label: str) -> bool:
        """Check if message has a specific label (case-insensitive)."""
        label_lower = label.lower()
        return any(l.lower() == label_lower for l in self.labels)


@dataclass
class AttachmentInfo:
    """Information about an email attachment."""

    filename: str
    content_type: str
    size: int
    content_disposition: str  # "attachment" or "inline"
    part_number: str  # MIME part reference (e.g., "2", "1.2")
    content_id: str | None = None  # For inline images (CID)
    encoding: str | None = None  # Content-Transfer-Encoding

    @property
    def is_inline(self) -> bool:
        """Check if this is an inline attachment (usually image)."""
        return self.content_disposition.lower() == "inline"

    @property
    def is_image(self) -> bool:
        """Check if this is an image attachment."""
        return self.content_type.lower().startswith("image/")

    @property
    def size_human(self) -> str:
        """Human-readable size string."""
        if self.size < 1024:
            return f"{self.size} B"
        elif self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        else:
            return f"{self.size / (1024 * 1024):.1f} MB"

    def __str__(self) -> str:
        """Human-readable representation."""
        return f"{self.filename} ({self.content_type}, {self.size_human})"


@dataclass
class EmailScanResult:
    """Complete scan result for a single email."""

    header: EmailHeader
    gmail_metadata: GmailMetadata
    attachments: list[AttachmentInfo]
    is_encrypted: bool = False
    mime_complexity: int = 1  # Nesting depth of MIME structure

    @property
    def total_attachment_size(self) -> int:
        """Total size of all attachments in bytes."""
        return sum(a.size for a in self.attachments)

    @property
    def strippable_attachments(self) -> list[AttachmentInfo]:
        """Attachments that can be stripped (not inline images)."""
        return [a for a in self.attachments if not a.is_inline]

    @property
    def strippable_size(self) -> int:
        """Total size of strippable attachments."""
        return sum(a.size for a in self.strippable_attachments)

    @property
    def can_process(self) -> bool:
        """Check if this email can be processed (not encrypted, has strippable attachments)."""
        return not self.is_encrypted and len(self.strippable_attachments) > 0


@dataclass
class SavedAttachment:
    """Record of a saved attachment after extraction."""

    original_filename: str
    saved_path: str  # Relative path in backup directory
    size: int
    content_type: str
    sha256_hash: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for manifest storage."""
        return {
            "filename": self.original_filename,
            "backup_path": self.saved_path,
            "size": self.size,
            "content_type": self.content_type,
            "hash": self.sha256_hash,
        }


@dataclass
class ExtractionResult:
    """Result of attachment extraction for a single email."""

    uid: int
    success: bool
    attachments_saved: list[SavedAttachment]
    errors: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        """Total bytes extracted."""
        return sum(a.size for a in self.attachments_saved)


@dataclass
class ValidationResult:
    """Result of reconstruction validation."""

    is_valid: bool
    original_size: int
    reconstructed_size: int
    header_issues: list[str] = field(default_factory=list)
    mime_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def size_reduction(self) -> int:
        """Bytes saved by stripping."""
        return self.original_size - self.reconstructed_size

    @property
    def size_reduction_percent(self) -> float:
        """Percentage reduction in size."""
        if self.original_size == 0:
            return 0.0
        return (self.size_reduction / self.original_size) * 100


@dataclass
class ReplaceResult:
    """Result of email replacement operation."""

    success: bool
    original_uid: int
    new_uid: int | None = None
    original_size: int = 0
    new_size: int = 0
    labels_applied: list[str] = field(default_factory=list)
    error: str | None = None
    phase_completed: str = ""  # Last successful phase: upload, verify, label, delete

    @property
    def size_saved(self) -> int:
        """Bytes saved by replacement."""
        return self.original_size - self.new_size


@dataclass
class ManifestEntry:
    """Single manifest entry for a processed email."""

    email_id: str  # Gmail message ID (X-GM-MSGID)
    imap_uid: int
    subject: str
    sender: str
    date: datetime
    labels: list[str]
    attachments: list[dict[str, Any]]  # List of SavedAttachment.to_dict()
    processed_at: datetime
    status: str  # pending, extracted, reconstructed, replaced, completed, failed, reverted
    original_size: int
    stripped_size: int | None = None
    error_message: str | None = None
    # Revert tracking fields
    stripped_uid: int | None = None  # UID of the stripped replacement email
    original_message_id: str | None = None  # Message-ID header for finding in Trash
    gmail_thread_id: str | None = None  # Thread ID to verify correct email

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "email_id": self.email_id,
            "imap_uid": self.imap_uid,
            "subject": self.subject,
            "sender": self.sender,
            "date": self.date.isoformat(),
            "labels": self.labels,
            "attachments": self.attachments,
            "processed_at": self.processed_at.isoformat(),
            "status": self.status,
            "original_size": self.original_size,
            "stripped_size": self.stripped_size,
            "error_message": self.error_message,
            "stripped_uid": self.stripped_uid,
            "original_message_id": self.original_message_id,
            "gmail_thread_id": self.gmail_thread_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ManifestEntry":
        """Create from dictionary."""
        return cls(
            email_id=data["email_id"],
            imap_uid=data["imap_uid"],
            subject=data["subject"],
            sender=data["sender"],
            date=datetime.fromisoformat(data["date"]),
            labels=data["labels"],
            attachments=data["attachments"],
            processed_at=datetime.fromisoformat(data["processed_at"]),
            status=data["status"],
            original_size=data["original_size"],
            stripped_size=data.get("stripped_size"),
            error_message=data.get("error_message"),
            stripped_uid=data.get("stripped_uid"),
            original_message_id=data.get("original_message_id"),
            gmail_thread_id=data.get("gmail_thread_id"),
        )

    @property
    def can_revert(self) -> bool:
        """Check if this entry can be reverted (original should be in Trash)."""
        return self.status == "completed" and self.original_message_id is not None


@dataclass
class ScanStatistics:
    """Aggregate statistics from email scanning."""

    total_emails: int
    total_attachments: int
    total_attachment_size: int
    emails_with_inline_only: int
    encrypted_emails_skipped: int
    by_content_type: dict[str, int] = field(default_factory=dict)
    by_year: dict[int, int] = field(default_factory=dict)
    by_sender: dict[str, int] = field(default_factory=dict)

    @property
    def processable_emails(self) -> int:
        """Number of emails that can be processed."""
        return self.total_emails - self.encrypted_emails_skipped - self.emails_with_inline_only

    @property
    def estimated_savings(self) -> int:
        """Estimated bytes that could be freed."""
        return self.total_attachment_size

    @property
    def estimated_savings_human(self) -> str:
        """Human-readable estimated savings."""
        size = self.estimated_savings
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.2f} GB"


@dataclass
class BatchResult:
    """Result of batch processing."""

    total_processed: int
    successful: int
    failed: int
    skipped: int
    total_bytes_saved: int
    errors: list[dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        """Percentage of successful operations."""
        if self.total_processed == 0:
            return 0.0
        return (self.successful / self.total_processed) * 100

    @property
    def bytes_saved_human(self) -> str:
        """Human-readable bytes saved."""
        size = self.total_bytes_saved
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.2f} GB"
