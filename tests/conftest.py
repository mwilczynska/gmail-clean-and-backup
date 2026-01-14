"""Pytest configuration and shared fixtures."""

import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models.email import (
    AttachmentInfo,
    EmailHeader,
    EmailScanResult,
    GmailMetadata,
)


@pytest.fixture
def sample_email_header() -> EmailHeader:
    """Create a sample email header for testing."""
    return EmailHeader(
        uid=12345,
        message_id="<test123@example.com>",
        subject="Test Email with Attachment",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        date=datetime(2024, 1, 15, 10, 30, 0),
        size=1048576,  # 1MB
        has_attachments=True,
    )


@pytest.fixture
def sample_gmail_metadata() -> GmailMetadata:
    """Create sample Gmail metadata for testing."""
    return GmailMetadata(
        gmail_message_id=123456789,
        gmail_thread_id=987654321,
        labels=["INBOX", "Important", "Work"],
    )


@pytest.fixture
def sample_attachment_info() -> AttachmentInfo:
    """Create a sample attachment info for testing."""
    return AttachmentInfo(
        filename="document.pdf",
        content_type="application/pdf",
        size=524288,  # 512KB
        content_disposition="attachment",
        part_number="2",
        encoding="BASE64",
    )


@pytest.fixture
def sample_scan_result(
    sample_email_header: EmailHeader,
    sample_gmail_metadata: GmailMetadata,
    sample_attachment_info: AttachmentInfo,
) -> EmailScanResult:
    """Create a sample scan result for testing."""
    return EmailScanResult(
        header=sample_email_header,
        gmail_metadata=sample_gmail_metadata,
        attachments=[sample_attachment_info],
        is_encrypted=False,
        mime_complexity=2,
    )


@pytest.fixture
def mock_imap_client():
    """Create a mock IMAP client for testing."""
    with patch("src.imap.client.GmailIMAPClient") as mock:
        client = MagicMock()
        mock.return_value = client

        # Setup default responses
        client.select_folder.return_value = 100
        client.search.return_value = [1, 2, 3, 4, 5]
        client.fetch_raw_email.return_value = b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"

        yield client


@pytest.fixture
def temp_backup_dir(tmp_path: Path) -> Path:
    """Create a temporary backup directory for testing."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    return backup_dir


@pytest.fixture
def temp_manifest_path(tmp_path: Path) -> Path:
    """Create a temporary manifest path for testing."""
    return tmp_path / "manifest.json"


@pytest.fixture
def sample_raw_email() -> bytes:
    """Create a sample raw email for testing."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email with Attachment
Date: Mon, 15 Jan 2024 10:30:00 +0000
Message-ID: <test123@example.com>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary123"

--boundary123
Content-Type: text/plain; charset="utf-8"

This is the email body.

--boundary123
Content-Type: application/pdf; name="document.pdf"
Content-Disposition: attachment; filename="document.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQKJeLjz9MKMSAwIG9iago8PC9UeXBlL0NhdGFsb2cvUGFnZXMgMiAwIFI+PgplbmRvYmoK
--boundary123--
"""


@pytest.fixture
def sample_encrypted_email() -> bytes:
    """Create a sample encrypted email for testing."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Encrypted Test
Date: Mon, 15 Jan 2024 10:30:00 +0000
Message-ID: <encrypted123@example.com>
MIME-Version: 1.0
Content-Type: application/pkcs7-mime; smime-type=enveloped-data; name="smime.p7m"
Content-Disposition: attachment; filename="smime.p7m"
Content-Transfer-Encoding: base64

MIIBhgYJKoZIhvcNAQcDoIIBdzCCAXMCAQAxggE...
"""


# Markers for test categories
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (may require network)"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests as slow running"
    )
