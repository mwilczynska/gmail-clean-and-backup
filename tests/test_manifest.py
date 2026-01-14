"""Tests for manifest management."""

import pytest
from datetime import datetime
from pathlib import Path

from src.utils.manifest import ManifestManager


class TestManifestManager:
    """Tests for ManifestManager class."""

    def test_record_extraction(self, temp_manifest_path: Path):
        """Test recording an extraction."""
        with ManifestManager(temp_manifest_path) as manifest:
            entry = manifest.record_extraction(
                email_id="123456",
                imap_uid=789,
                subject="Test Subject",
                sender="test@example.com",
                date=datetime(2024, 1, 15),
                labels=["INBOX", "Important"],
                attachments=[{"filename": "test.pdf", "size": 1024}],
                original_size=10240,
            )

            assert entry.email_id == "123456"
            assert entry.status == "extracted"

    def test_get_entry(self, temp_manifest_path: Path):
        """Test retrieving an entry."""
        with ManifestManager(temp_manifest_path) as manifest:
            # Record an entry
            manifest.record_extraction(
                email_id="test123",
                imap_uid=456,
                subject="Test",
                sender="test@example.com",
                date=datetime.now(),
                labels=[],
                attachments=[],
                original_size=1024,
            )

            # Retrieve it
            entry = manifest.get_entry("test123")
            assert entry is not None
            assert entry.email_id == "test123"

            # Non-existent entry
            assert manifest.get_entry("nonexistent") is None

    def test_update_status(self, temp_manifest_path: Path):
        """Test updating entry status."""
        with ManifestManager(temp_manifest_path) as manifest:
            manifest.record_extraction(
                email_id="status_test",
                imap_uid=1,
                subject="Test",
                sender="test@example.com",
                date=datetime.now(),
                labels=[],
                attachments=[],
                original_size=1024,
            )

            # Update status
            manifest.update_status("status_test", "completed", stripped_size=512)

            # Verify update
            entry = manifest.get_entry("status_test")
            assert entry.status == "completed"
            assert entry.stripped_size == 512

    def test_get_entries_by_status(self, temp_manifest_path: Path):
        """Test filtering entries by status."""
        with ManifestManager(temp_manifest_path) as manifest:
            # Create multiple entries with different statuses
            for i in range(5):
                manifest.record_extraction(
                    email_id=f"test{i}",
                    imap_uid=i,
                    subject=f"Test {i}",
                    sender="test@example.com",
                    date=datetime.now(),
                    labels=[],
                    attachments=[],
                    original_size=1024,
                )

            # Update some to completed
            manifest.update_status("test0", "completed")
            manifest.update_status("test1", "completed")
            manifest.update_status("test2", "failed")

            # Query by status
            completed = manifest.get_entries_by_status("completed")
            assert len(completed) == 2

            failed = manifest.get_entries_by_status("failed")
            assert len(failed) == 1

            extracted = manifest.get_entries_by_status("extracted")
            assert len(extracted) == 2

    def test_is_processed(self, temp_manifest_path: Path):
        """Test checking if email is processed."""
        with ManifestManager(temp_manifest_path) as manifest:
            manifest.record_extraction(
                email_id="processed_test",
                imap_uid=1,
                subject="Test",
                sender="test@example.com",
                date=datetime.now(),
                labels=[],
                attachments=[],
                original_size=1024,
            )

            # Not completed yet
            assert manifest.is_processed("processed_test") is False

            # Mark as completed
            manifest.update_status("processed_test", "completed")
            assert manifest.is_processed("processed_test") is True

    def test_get_processing_stats(self, temp_manifest_path: Path):
        """Test getting processing statistics."""
        with ManifestManager(temp_manifest_path) as manifest:
            # Create entries
            for i in range(10):
                manifest.record_extraction(
                    email_id=f"stats{i}",
                    imap_uid=i,
                    subject=f"Test {i}",
                    sender="test@example.com",
                    date=datetime.now(),
                    labels=[],
                    attachments=[{"filename": "test.pdf"}],
                    original_size=1024 * (i + 1),
                )

            stats = manifest.get_processing_stats()

            assert stats["total"] == 10
            assert stats["total_attachments"] == 10
            assert stats["total_original_size"] > 0

    def test_export_manifest_json(self, temp_manifest_path: Path, tmp_path: Path):
        """Test exporting manifest to JSON."""
        with ManifestManager(temp_manifest_path) as manifest:
            manifest.record_extraction(
                email_id="export_test",
                imap_uid=1,
                subject="Test",
                sender="test@example.com",
                date=datetime.now(),
                labels=["INBOX"],
                attachments=[],
                original_size=1024,
            )

            export_path = tmp_path / "export.json"
            manifest.export_manifest(export_path, format="json")

            assert export_path.exists()
            content = export_path.read_text()
            assert "export_test" in content

    def test_export_manifest_csv(self, temp_manifest_path: Path, tmp_path: Path):
        """Test exporting manifest to CSV."""
        with ManifestManager(temp_manifest_path) as manifest:
            manifest.record_extraction(
                email_id="csv_test",
                imap_uid=1,
                subject="Test CSV",
                sender="test@example.com",
                date=datetime.now(),
                labels=[],
                attachments=[],
                original_size=1024,
            )

            export_path = tmp_path / "export.csv"
            manifest.export_manifest(export_path, format="csv")

            assert export_path.exists()
            content = export_path.read_text()
            assert "csv_test" in content
            assert "Test CSV" in content
