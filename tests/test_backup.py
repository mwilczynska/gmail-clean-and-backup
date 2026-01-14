"""Tests for backup management."""

import pytest
from datetime import datetime
from pathlib import Path

from src.models.email import EmailHeader
from src.processor.backup import BackupManager


class TestBackupManager:
    """Tests for BackupManager class."""

    def test_get_backup_path_by_date(self, temp_backup_dir: Path):
        """Test date-based backup path generation."""
        manager = BackupManager(temp_backup_dir, organize_by="date")

        header = EmailHeader(
            uid=1,
            message_id="<test@example.com>",
            subject="Test Subject",
            sender="sender@example.com",
            recipients=[],
            date=datetime(2024, 1, 15),
            size=1024,
            has_attachments=True,
        )

        path = manager.get_backup_path(header, "document.pdf")

        # Should contain year/month/day structure
        assert "2024" in str(path)
        assert "01" in str(path)
        assert "15" in str(path)
        assert path.name == "document.pdf"

    def test_sanitize_filename(self, temp_backup_dir: Path):
        """Test filename sanitization."""
        manager = BackupManager(temp_backup_dir)

        # Test various problematic characters
        assert manager._sanitize_filename("normal.pdf") == "normal.pdf"
        assert manager._sanitize_filename("file:with:colons.pdf") == "file_with_colons.pdf"
        assert manager._sanitize_filename("file<>with.pdf") == "file__with.pdf"
        assert manager._sanitize_filename("") == "attachment"

    def test_sanitize_for_path(self, temp_backup_dir: Path):
        """Test path component sanitization."""
        manager = BackupManager(temp_backup_dir)

        assert manager._sanitize_for_path("Normal Subject") == "Normal_Subject"
        assert manager._sanitize_for_path("Subject: with colons") == "Subject__with_colons"
        assert manager._sanitize_for_path("") == "unknown"

    def test_save_attachment(self, temp_backup_dir: Path):
        """Test attachment saving."""
        manager = BackupManager(temp_backup_dir)

        data = b"Test file content"
        path = temp_backup_dir / "test.txt"

        saved = manager.save_attachment(
            data=data,
            path=path,
            original_filename="test.txt",
            content_type="text/plain",
        )

        assert path.exists()
        assert saved.size == len(data)
        assert saved.sha256_hash.startswith("sha256:")
        assert path.read_bytes() == data

    def test_handle_duplicate(self, temp_backup_dir: Path):
        """Test duplicate filename handling."""
        manager = BackupManager(temp_backup_dir)

        # Create existing file
        existing = temp_backup_dir / "test.pdf"
        existing.write_bytes(b"existing")

        # Get path for duplicate
        new_path = manager._handle_duplicate(existing)

        assert new_path != existing
        assert new_path.stem == "test_1"
        assert new_path.suffix == ".pdf"

    def test_verify_backup(self, temp_backup_dir: Path):
        """Test backup verification."""
        manager = BackupManager(temp_backup_dir)

        data = b"Test content for verification"
        path = temp_backup_dir / "verify_test.txt"

        saved = manager.save_attachment(
            data=data,
            path=path,
            original_filename="verify_test.txt",
            content_type="text/plain",
        )

        # Should verify successfully
        assert manager.verify_backup(saved) is True

        # Corrupt the file
        path.write_bytes(b"corrupted content")

        # Should fail verification
        assert manager.verify_backup(saved) is False
