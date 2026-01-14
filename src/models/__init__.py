"""Data models for email processing."""

from src.models.email import (
    AttachmentInfo,
    EmailHeader,
    EmailScanResult,
    GmailMetadata,
)

__all__ = ["EmailHeader", "GmailMetadata", "AttachmentInfo", "EmailScanResult"]
