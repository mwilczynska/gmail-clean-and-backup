"""Tests for the MIME pipeline: reconstruction, validation and pre-flight checks.

These cover the part of the tool that rewrites a message. A fault here is the
expensive kind - it detaches mail from its thread or drops body content - and
unlike a failed upload it is not obvious after the fact, so the assertions below
are deliberately about what must survive a rewrite rather than about how the
rewrite is implemented.
"""

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

from src.models.email import AttachmentInfo, SavedAttachment
from src.processor.reconstructor import (
    EmailReconstructor,
    MIMETreeWalker,
)
from src.processor.validator import PreflightChecker, ReconstructionValidator

PDF_BYTES = b"%PDF-1.4 pretend document body " * 200
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"pretend image data " * 50


def _headers(msg: EmailMessage) -> None:
    """Apply a realistic threaded header set to a message."""
    msg["Message-ID"] = "<child@example.com>"
    msg["Date"] = "Mon, 5 Mar 2018 19:29:00 +1100"
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Quarterly report"
    msg["In-Reply-To"] = "<parent@example.com>"
    msg["References"] = "<root@example.com> <parent@example.com>"


def make_email_with_attachment() -> bytes:
    """multipart/mixed: a text body plus a single PDF attachment."""
    msg = EmailMessage()
    _headers(msg)
    msg.set_content("Here is the report you asked for.\n")
    msg.add_attachment(
        PDF_BYTES, maintype="application", subtype="pdf", filename="report.pdf"
    )
    return msg.as_bytes()


def make_email_with_inline_image() -> bytes:
    """A message carrying an inline image with a Content-ID, plus a real attachment."""
    msg = EmailMessage()
    _headers(msg)
    msg.set_content("See the logo below.\n")
    msg.add_attachment(
        PNG_BYTES,
        maintype="image",
        subtype="png",
        filename="logo.png",
        cid="<logo@example.com>",
    )
    msg.add_attachment(
        PDF_BYTES, maintype="application", subtype="pdf", filename="report.pdf"
    )
    return msg.as_bytes()


def make_nested_email() -> bytes:
    """multipart/mixed wrapping a multipart/alternative body, plus an attachment."""
    msg = EmailMessage()
    _headers(msg)
    msg.set_content("Plain text version.\n")
    msg.add_alternative("<html><body><p>HTML version.</p></body></html>", subtype="html")
    msg.add_attachment(
        PDF_BYTES, maintype="application", subtype="pdf", filename="report.pdf"
    )
    return msg.as_bytes()


def pdf_attachment_info(filename: str = "report.pdf") -> AttachmentInfo:
    """AttachmentInfo matching the PDF used by the builders above."""
    return AttachmentInfo(
        filename=filename,
        content_type="application/pdf",
        size=len(PDF_BYTES),
        content_disposition="attachment",
        part_number="2",
        encoding="base64",
    )


def saved_record(filename: str = "report.pdf") -> SavedAttachment:
    """SavedAttachment record pointing at a plausible backup path."""
    return SavedAttachment(
        original_filename=filename,
        saved_path=f"backups/documents/2018-03-05_{filename}",
        size=len(PDF_BYTES),
        content_type="application/pdf",
        sha256_hash="sha256:" + "ab" * 32,
    )


def parse(raw: bytes) -> EmailMessage:
    """Parse raw bytes with the same policy the tool uses."""
    return BytesParser(policy=policy.default).parsebytes(raw)


def carries_payload(raw: bytes, needle: bytes) -> bool:
    """True if any leaf part's *decoded* payload contains needle.

    Attachment bodies are base64 encoded, so searching the raw bytes for the
    original content would silently pass whether or not the attachment was
    removed. Every check for surviving attachment content goes through here.
    """
    for part in parse(raw).walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        if payload and needle in payload:
            return True
    return False


def attachment_filenames(raw: bytes) -> list[str]:
    """Filenames of parts still marked as attachments."""
    return [
        part.get_filename()
        for part in parse(raw).walk()
        if part.get_content_disposition() == "attachment"
    ]


class TestEmailReconstructor:
    """Tests for stripping attachments out of a message."""

    def test_removes_attachment_payload(self):
        """The attachment bytes are gone and the message shrinks accordingly."""
        raw = make_email_with_attachment()
        out = EmailReconstructor().reconstruct(
            raw, [pdf_attachment_info()], [saved_record()]
        )

        assert len(out) < len(raw) / 2
        assert not carries_payload(out, b"%PDF-1.4")
        assert "report.pdf" not in attachment_filenames(out)

    def test_preserves_threading_headers(self):
        """Threading headers must survive exactly, or the reply chain breaks."""
        raw = make_email_with_attachment()
        out = EmailReconstructor().reconstruct(
            raw, [pdf_attachment_info()], [saved_record()]
        )

        original, rebuilt = parse(raw), parse(out)
        for header in ("Message-ID", "Date", "From", "Subject", "In-Reply-To", "References"):
            assert rebuilt.get(header) == original.get(header), f"{header} changed"

    def test_preserves_body_text(self):
        """The human-written body is not collateral damage."""
        raw = make_email_with_attachment()
        out = EmailReconstructor().reconstruct(
            raw, [pdf_attachment_info()], [saved_record()]
        )

        assert "Here is the report you asked for." in out.decode("utf-8", errors="replace")

    def test_inserts_placeholder_naming_file_and_backup_path(self):
        """The placeholder has to say what was removed and where it went."""
        raw = make_email_with_attachment()
        out = EmailReconstructor().reconstruct(
            raw, [pdf_attachment_info()], [saved_record()]
        )

        text = out.decode("utf-8", errors="replace")
        assert "[Attachment Removed]" in text
        assert "report.pdf" in text
        assert "backups/documents/2018-03-05_report.pdf" in text

    def test_output_remains_parseable_multipart(self):
        """A rewritten message must still be a well-formed MIME document."""
        raw = make_email_with_attachment()
        out = EmailReconstructor().reconstruct(
            raw, [pdf_attachment_info()], [saved_record()]
        )

        rebuilt = parse(out)
        assert rebuilt.get("Content-Type") is not None
        assert rebuilt.is_multipart()
        assert rebuilt.get_boundary()

    def test_leaves_unnamed_attachments_alone(self):
        """Only the attachments explicitly listed are stripped."""
        raw = make_email_with_attachment()
        out = EmailReconstructor().reconstruct(
            raw, [pdf_attachment_info("something-else.pdf")], []
        )

        assert carries_payload(out, b"%PDF-1.4")
        assert attachment_filenames(out) == ["report.pdf"]
        assert "[Attachment Removed]" not in out.decode("utf-8", errors="replace")

    def test_preserves_inline_image_by_default(self):
        """Inline images are referenced by the HTML body, so they are kept."""
        raw = make_email_with_inline_image()
        to_strip = [pdf_attachment_info(), pdf_attachment_info("logo.png")]
        out = EmailReconstructor(preserve_inline=True).reconstruct(
            raw, to_strip, [saved_record()]
        )

        assert carries_payload(out, PNG_BYTES[:8]), "inline image was dropped"
        assert not carries_payload(out, b"%PDF-1.4")

    def test_strips_inline_image_when_disabled(self):
        """With preserve_inline off, the inline image goes too."""
        raw = make_email_with_inline_image()
        to_strip = [pdf_attachment_info(), pdf_attachment_info("logo.png")]
        out = EmailReconstructor(preserve_inline=False).reconstruct(
            raw, to_strip, [saved_record()]
        )

        assert not carries_payload(out, PNG_BYTES[:8]), "inline image was kept"
        assert not carries_payload(out, b"%PDF-1.4")
        assert "[Attachment Removed]" in out.decode("utf-8", errors="replace")

    def test_handles_nested_multipart(self):
        """A multipart/alternative body nested in multipart/mixed survives intact."""
        raw = make_nested_email()
        out = EmailReconstructor().reconstruct(
            raw, [pdf_attachment_info()], [saved_record()]
        )

        text = out.decode("utf-8", errors="replace")
        assert "Plain text version." in text
        assert "HTML version." in text
        assert not carries_payload(out, b"%PDF-1.4")
        assert parse(out).is_multipart()

    def test_custom_placeholder_template(self):
        """A caller-supplied template is used verbatim."""
        template = "REMOVED {filename} -> {backup_path}"
        out = EmailReconstructor(placeholder_template=template).reconstruct(
            make_email_with_attachment(), [pdf_attachment_info()], [saved_record()]
        )

        text = out.decode("utf-8", errors="replace")
        assert "REMOVED report.pdf -> backups/documents/2018-03-05_report.pdf" in text
        assert "[Attachment Removed]" not in text

    def test_message_without_attachments_keeps_its_body(self):
        """Reconstructing a message with nothing to strip is non-destructive."""
        msg = EmailMessage()
        _headers(msg)
        msg.set_content("Nothing attached here.\n")
        raw = msg.as_bytes()

        out = EmailReconstructor().reconstruct(raw, [], [])

        assert "Nothing attached here." in out.decode("utf-8", errors="replace")
        assert parse(out).get("Message-ID") == "<child@example.com>"


class TestReconstructionValidator:
    """Tests for the gate that runs before anything is uploaded or deleted."""

    def test_accepts_faithful_reconstruction(self):
        """A correct rewrite passes with no issues."""
        raw = make_email_with_attachment()
        out = EmailReconstructor().reconstruct(
            raw, [pdf_attachment_info()], [saved_record()]
        )

        result = ReconstructionValidator().validate(raw, out)

        assert result.is_valid
        assert result.header_issues == []
        assert result.mime_issues == []
        assert result.reconstructed_size < result.original_size

    def test_rejects_modified_critical_header(self):
        """A rewritten Subject must fail the gate rather than reach Gmail."""
        raw = make_email_with_attachment()
        tampered = raw.replace(b"Subject: Quarterly report", b"Subject: Something else")

        result = ReconstructionValidator().validate(raw, tampered)

        assert not result.is_valid
        assert any("Subject" in issue for issue in result.header_issues)

    def test_rejects_dropped_references_header(self):
        """Losing References silently detaches the mail from its thread."""
        raw = make_email_with_attachment()
        stripped = raw.replace(
            b"References: <root@example.com> <parent@example.com>\n", b""
        )

        result = ReconstructionValidator().validate(raw, stripped)

        assert not result.is_valid
        assert any("References" in issue for issue in result.header_issues)

    def test_quick_validate_requires_identifying_headers(self):
        """The cheap check still insists on Message-ID, Date and From."""
        validator = ReconstructionValidator()

        assert validator.quick_validate(make_email_with_attachment())
        assert not validator.quick_validate(b"Subject: no identifying headers\n\nbody")


class TestPreflightChecker:
    """Tests for the checks that decide whether a message may be processed."""

    def test_accepts_ordinary_email(self):
        """A normal message with an attachment is processable."""
        can_process, reasons = PreflightChecker.can_process(make_email_with_attachment())

        assert can_process
        assert reasons == []

    def test_rejects_encrypted_email(self, sample_encrypted_email: bytes):
        """S/MIME cannot be rewritten without destroying the signature."""
        can_process, reasons = PreflightChecker.can_process(sample_encrypted_email)

        assert not can_process
        assert any("encrypted" in reason.lower() for reason in reasons)

    def test_rejects_email_without_message_id(self):
        """Without a Message-ID the original could never be found again to revert."""
        msg = EmailMessage()
        msg["From"] = "sender@example.com"
        msg["Subject"] = "No identity"
        msg.set_content("body")

        can_process, reasons = PreflightChecker.can_process(msg.as_bytes())

        assert not can_process
        assert any("Message-ID" in reason for reason in reasons)


class TestMIMETreeWalker:
    """Tests for the MIME tree helpers used during reconstruction."""

    def test_counts_parts_of_flat_message(self):
        """A body plus one attachment is two parts at depth one."""
        msg = parse(make_email_with_attachment())

        # Counts the multipart/mixed root plus its two children
        assert MIMETreeWalker.count_parts(msg) == 3
        # A leaf is depth 1, so a multipart root over leaf children is depth 2
        assert MIMETreeWalker.get_depth(msg) == 2

    def test_nested_message_is_deeper(self):
        """Nesting multipart/alternative inside mixed increases the depth."""
        flat = parse(make_email_with_attachment())
        nested = parse(make_nested_email())

        assert MIMETreeWalker.get_depth(nested) > MIMETreeWalker.get_depth(flat)

    def test_finds_text_parts_only(self):
        """Text parts are located; the binary attachment is not among them."""
        msg = parse(make_email_with_attachment())

        text_parts = MIMETreeWalker.find_text_parts(msg)

        assert len(text_parts) >= 1
        assert all(part.get_content_maintype() == "text" for part in text_parts)
