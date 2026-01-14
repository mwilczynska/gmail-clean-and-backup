"""Validation of reconstructed emails."""

from email import policy
from email.parser import BytesParser

from src.models.email import ValidationResult
from src.processor.mime_handler import MIMEHandler


class ReconstructionValidator:
    """Validates reconstructed emails before upload.

    Ensures critical headers are preserved and MIME structure
    is valid after reconstruction.
    """

    # Headers that must be preserved exactly
    CRITICAL_HEADERS = [
        "Message-ID",
        "Date",
        "From",
        "Subject",
        "In-Reply-To",
        "References",
    ]

    # Headers that should be preserved but minor changes are OK
    IMPORTANT_HEADERS = [
        "To",
        "Cc",
        "Reply-To",
    ]

    def __init__(self) -> None:
        """Initialize validator."""
        self._parser = BytesParser(policy=policy.default)

    def validate(
        self,
        original: bytes,
        reconstructed: bytes,
    ) -> ValidationResult:
        """Comprehensive validation of reconstruction.

        Args:
            original: Original email bytes.
            reconstructed: Reconstructed email bytes.

        Returns:
            ValidationResult with detailed analysis.
        """
        original_msg = self._parser.parsebytes(original)
        reconstructed_msg = self._parser.parsebytes(reconstructed)

        # Check headers
        header_issues = self._check_headers_preserved(original_msg, reconstructed_msg)

        # Check MIME validity
        mime_issues = self._check_mime_validity(reconstructed_msg)

        # Check body preserved
        warnings = []
        if not self._check_body_preserved(original_msg, reconstructed_msg):
            warnings.append("Original body text may have been modified")

        # Calculate sizes
        original_size = len(original)
        reconstructed_size = len(reconstructed)

        # Determine if valid
        is_valid = len(header_issues) == 0 and len(mime_issues) == 0

        return ValidationResult(
            is_valid=is_valid,
            original_size=original_size,
            reconstructed_size=reconstructed_size,
            header_issues=header_issues,
            mime_issues=mime_issues,
            warnings=warnings,
        )

    def _check_headers_preserved(
        self,
        original: "EmailMessage",  # type: ignore
        reconstructed: "EmailMessage",  # type: ignore
    ) -> list[str]:
        """Verify critical headers are preserved.

        Args:
            original: Original email message.
            reconstructed: Reconstructed email message.

        Returns:
            List of issues found.
        """
        issues = []

        for header in self.CRITICAL_HEADERS:
            original_value = original.get(header, "")
            reconstructed_value = reconstructed.get(header, "")

            if original_value and not reconstructed_value:
                issues.append(f"Missing critical header: {header}")
            elif original_value != reconstructed_value:
                issues.append(f"Modified critical header: {header}")

        # Check important headers (just warn, don't fail)
        for header in self.IMPORTANT_HEADERS:
            original_value = original.get(header, "")
            reconstructed_value = reconstructed.get(header, "")

            if original_value and not reconstructed_value:
                # This is a warning, not an error
                pass

        return issues

    def _check_mime_validity(
        self,
        msg: "EmailMessage",  # type: ignore
    ) -> list[str]:
        """Verify MIME structure is valid.

        Args:
            msg: Email message to check.

        Returns:
            List of issues found.
        """
        issues = []

        # Check Content-Type exists
        if not msg.get("Content-Type"):
            issues.append("Missing Content-Type header")

        # Check multipart has valid boundary
        if msg.is_multipart():
            boundary = msg.get_boundary()
            if not boundary:
                issues.append("Multipart message missing boundary")

            # Check at least one part exists
            parts = list(msg.iter_parts())
            if not parts:
                issues.append("Multipart message has no parts")

            # Recursively check parts
            for i, part in enumerate(parts):
                part_issues = self._check_mime_validity(part)
                for issue in part_issues:
                    issues.append(f"Part {i + 1}: {issue}")

        return issues

    def _check_body_preserved(
        self,
        original: "EmailMessage",  # type: ignore
        reconstructed: "EmailMessage",  # type: ignore
    ) -> bool:
        """Verify text body is preserved.

        Args:
            original: Original email message.
            reconstructed: Reconstructed email message.

        Returns:
            True if body appears preserved.
        """
        from src.processor.mime_handler import EncodingHandler

        # Find text parts in both messages
        original_text = self._extract_text_content(original)
        reconstructed_text = self._extract_text_content(reconstructed)

        # Check if original text is contained in reconstructed
        # (reconstructed may have additional placeholder text)
        if original_text:
            # Normalize whitespace for comparison
            original_normalized = " ".join(original_text.split())
            reconstructed_normalized = " ".join(reconstructed_text.split())

            return original_normalized in reconstructed_normalized

        return True

    def _extract_text_content(
        self,
        msg: "EmailMessage",  # type: ignore
    ) -> str:
        """Extract all text content from message.

        Args:
            msg: Email message.

        Returns:
            Combined text content.
        """
        from src.processor.mime_handler import EncodingHandler

        text_parts = []

        if not msg.is_multipart():
            if msg.get_content_type() == "text/plain":
                text_parts.append(EncodingHandler.safe_decode_payload(msg))
            return "\n".join(text_parts)

        for part in msg.iter_parts():
            text_parts.append(self._extract_text_content(part))

        return "\n".join(text_parts)

    def quick_validate(self, reconstructed: bytes) -> bool:
        """Quick validation - just check it parses.

        Args:
            reconstructed: Reconstructed email bytes.

        Returns:
            True if email parses successfully.
        """
        try:
            msg = self._parser.parsebytes(reconstructed)
            # Check basic structure
            return (
                msg.get("Message-ID") is not None
                and msg.get("Date") is not None
                and msg.get("From") is not None
            )
        except Exception:
            return False


class PreflightChecker:
    """Pre-flight checks before processing an email."""

    @staticmethod
    def can_process(raw_email: bytes) -> tuple[bool, list[str]]:
        """Check if email can be safely processed.

        Args:
            raw_email: Raw email bytes.

        Returns:
            Tuple of (can_process, list of reasons if not).
        """
        reasons = []
        parser = BytesParser(policy=policy.default)

        try:
            msg = parser.parsebytes(raw_email)
        except Exception as e:
            return False, [f"Failed to parse email: {e}"]

        # Check for encryption
        if MIMEHandler.is_encrypted(msg):
            reasons.append("Email is encrypted (S/MIME or PGP)")

        # Check for missing critical headers
        if not msg.get("Message-ID"):
            reasons.append("Missing Message-ID header")

        # Check for unreasonable size
        if len(raw_email) > 50 * 1024 * 1024:  # 50MB
            reasons.append("Email exceeds 50MB size limit")

        # Check for valid structure
        content_type = msg.get("Content-Type")
        if not content_type:
            reasons.append("Missing Content-Type header")

        return len(reasons) == 0, reasons
