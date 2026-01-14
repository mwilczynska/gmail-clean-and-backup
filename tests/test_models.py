"""Tests for data models."""

import pytest
from datetime import datetime

from src.models.email import (
    AttachmentInfo,
    EmailHeader,
    EmailScanResult,
    GmailMetadata,
    ManifestEntry,
    ScanStatistics,
)


class TestAttachmentInfo:
    """Tests for AttachmentInfo dataclass."""

    def test_is_inline(self):
        """Test inline detection."""
        inline = AttachmentInfo(
            filename="image.png",
            content_type="image/png",
            size=1024,
            content_disposition="inline",
            part_number="1",
        )
        attachment = AttachmentInfo(
            filename="doc.pdf",
            content_type="application/pdf",
            size=1024,
            content_disposition="attachment",
            part_number="2",
        )

        assert inline.is_inline is True
        assert attachment.is_inline is False

    def test_is_image(self):
        """Test image detection."""
        image = AttachmentInfo(
            filename="photo.jpg",
            content_type="image/jpeg",
            size=1024,
            content_disposition="attachment",
            part_number="1",
        )
        pdf = AttachmentInfo(
            filename="doc.pdf",
            content_type="application/pdf",
            size=1024,
            content_disposition="attachment",
            part_number="2",
        )

        assert image.is_image is True
        assert pdf.is_image is False

    def test_size_human(self):
        """Test human-readable size formatting."""
        small = AttachmentInfo("f", "t", 512, "a", "1")
        medium = AttachmentInfo("f", "t", 1024 * 512, "a", "1")
        large = AttachmentInfo("f", "t", 1024 * 1024 * 2, "a", "1")

        assert "512 B" in small.size_human
        assert "KB" in medium.size_human
        assert "MB" in large.size_human


class TestEmailScanResult:
    """Tests for EmailScanResult dataclass."""

    def test_strippable_attachments(self, sample_scan_result: EmailScanResult):
        """Test filtering of strippable attachments."""
        # Default fixture has one attachment disposition
        assert len(sample_scan_result.strippable_attachments) == 1

    def test_can_process_encrypted(self, sample_scan_result: EmailScanResult):
        """Test can_process is False for encrypted emails."""
        sample_scan_result.is_encrypted = True
        assert sample_scan_result.can_process is False

    def test_can_process_no_attachments(
        self,
        sample_email_header: EmailHeader,
        sample_gmail_metadata: GmailMetadata,
    ):
        """Test can_process is False when no strippable attachments."""
        result = EmailScanResult(
            header=sample_email_header,
            gmail_metadata=sample_gmail_metadata,
            attachments=[],
        )
        assert result.can_process is False


class TestGmailMetadata:
    """Tests for GmailMetadata dataclass."""

    def test_has_label(self, sample_gmail_metadata: GmailMetadata):
        """Test label checking."""
        assert sample_gmail_metadata.has_label("INBOX") is True
        assert sample_gmail_metadata.has_label("inbox") is True  # Case insensitive
        assert sample_gmail_metadata.has_label("NonExistent") is False


class TestManifestEntry:
    """Tests for ManifestEntry dataclass."""

    def test_to_dict_roundtrip(self):
        """Test conversion to dict and back."""
        entry = ManifestEntry(
            email_id="123",
            imap_uid=456,
            subject="Test Subject",
            sender="test@example.com",
            date=datetime(2024, 1, 15),
            labels=["INBOX"],
            attachments=[{"filename": "test.pdf", "size": 1024}],
            processed_at=datetime(2024, 1, 16),
            status="completed",
            original_size=10240,
            stripped_size=1024,
        )

        data = entry.to_dict()
        restored = ManifestEntry.from_dict(data)

        assert restored.email_id == entry.email_id
        assert restored.subject == entry.subject
        assert restored.status == entry.status


class TestScanStatistics:
    """Tests for ScanStatistics dataclass."""

    def test_estimated_savings_human(self):
        """Test human-readable savings formatting."""
        stats = ScanStatistics(
            total_emails=100,
            total_attachments=200,
            total_attachment_size=1024 * 1024 * 500,  # 500MB
            emails_with_inline_only=10,
            encrypted_emails_skipped=5,
        )

        assert "MB" in stats.estimated_savings_human or "GB" in stats.estimated_savings_human

    def test_processable_emails(self):
        """Test processable email count."""
        stats = ScanStatistics(
            total_emails=100,
            total_attachments=200,
            total_attachment_size=1024 * 1024,
            emails_with_inline_only=10,
            encrypted_emails_skipped=5,
        )

        assert stats.processable_emails == 85  # 100 - 10 - 5
