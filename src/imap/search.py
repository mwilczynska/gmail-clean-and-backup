"""Gmail search functionality with IMAP extensions."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.imap.client import GmailIMAPClient


@dataclass
class SearchCriteria:
    """Email search criteria container.

    Supports both standard IMAP search and Gmail-specific X-GM-RAW queries.
    """

    # Attachment filter
    has_attachment: bool = True

    # Size filters (in bytes)
    min_size: int | None = None
    max_size: int | None = None

    # Date filters
    before_date: datetime | None = None
    after_date: datetime | None = None

    # Sender filter
    from_sender: str | None = None

    # Label filters
    labels: list[str] = field(default_factory=list)
    exclude_labels: list[str] = field(default_factory=list)

    # Raw Gmail search query (takes precedence if set)
    # Supports full Gmail search syntax: has:attachment larger:5M older_than:2y
    gmail_raw_query: str | None = None

    def to_imap_criteria(self) -> str:
        """Convert to IMAP SEARCH criteria string.

        Returns:
            IMAP search string using Gmail extensions where applicable.
        """
        # If raw query is provided, use X-GM-RAW
        if self.gmail_raw_query:
            return f'X-GM-RAW "{self.gmail_raw_query}"'

        # Build standard IMAP criteria
        criteria_parts: list[str] = []

        # Gmail's X-GM-RAW is more reliable for attachment search
        if self.has_attachment:
            criteria_parts.append('X-GM-RAW "has:attachment"')

        # Size filters using X-GM-RAW (more reliable than LARGER/SMALLER)
        if self.min_size:
            size_mb = self.min_size / (1024 * 1024)
            if size_mb >= 1:
                criteria_parts.append(f'X-GM-RAW "larger:{int(size_mb)}M"')
            else:
                size_kb = self.min_size / 1024
                criteria_parts.append(f'X-GM-RAW "larger:{int(size_kb)}K"')

        if self.max_size:
            size_mb = self.max_size / (1024 * 1024)
            if size_mb >= 1:
                criteria_parts.append(f'X-GM-RAW "smaller:{int(size_mb)}M"')
            else:
                size_kb = self.max_size / 1024
                criteria_parts.append(f'X-GM-RAW "smaller:{int(size_kb)}K"')

        # Date filters
        if self.before_date:
            date_str = self.before_date.strftime("%d-%b-%Y")
            criteria_parts.append(f"BEFORE {date_str}")

        if self.after_date:
            date_str = self.after_date.strftime("%d-%b-%Y")
            criteria_parts.append(f"SINCE {date_str}")

        # Sender filter
        if self.from_sender:
            criteria_parts.append(f'FROM "{self.from_sender}"')

        # Label filters using X-GM-RAW
        for label in self.labels:
            criteria_parts.append(f'X-GM-RAW "label:{label}"')

        for label in self.exclude_labels:
            criteria_parts.append(f'X-GM-RAW "-label:{label}"')

        # Combine all criteria
        if not criteria_parts:
            return "ALL"

        return " ".join(criteria_parts)


class GmailSearcher:
    """Gmail-specific search operations using IMAP extensions."""

    def __init__(self, client: "GmailIMAPClient") -> None:
        """Initialize with connected IMAP client.

        Args:
            client: Connected and authenticated GmailIMAPClient.
        """
        self.client = client

    def search(self, criteria: SearchCriteria) -> list[int]:
        """Execute search and return list of UIDs.

        Args:
            criteria: Search criteria to apply.

        Returns:
            List of message UIDs matching criteria.
        """
        imap_criteria = criteria.to_imap_criteria()
        return self.client.search(imap_criteria)

    def search_raw(self, gmail_query: str) -> list[int]:
        """Execute raw Gmail search query.

        Args:
            gmail_query: Gmail search syntax query
                (e.g., "has:attachment larger:5M older_than:2y").

        Returns:
            List of message UIDs matching query.
        """
        criteria = SearchCriteria(gmail_raw_query=gmail_query)
        return self.search(criteria)

    def count_matching(self, criteria: SearchCriteria) -> int:
        """Return count of matching emails.

        Args:
            criteria: Search criteria to apply.

        Returns:
            Number of messages matching criteria.
        """
        return len(self.search(criteria))

    def search_with_attachments_larger_than(
        self,
        size_bytes: int,
        before_date: datetime | None = None,
    ) -> list[int]:
        """Search for emails with attachments larger than specified size.

        Args:
            size_bytes: Minimum attachment size in bytes.
            before_date: Optional date filter for older emails.

        Returns:
            List of message UIDs.
        """
        criteria = SearchCriteria(
            has_attachment=True,
            min_size=size_bytes,
            before_date=before_date,
        )
        return self.search(criteria)

    def search_by_sender(
        self,
        sender: str,
        has_attachment: bool = True,
    ) -> list[int]:
        """Search for emails from specific sender.

        Args:
            sender: Sender email address or name.
            has_attachment: If True, only return emails with attachments.

        Returns:
            List of message UIDs.
        """
        criteria = SearchCriteria(
            has_attachment=has_attachment,
            from_sender=sender,
        )
        return self.search(criteria)

    def search_in_date_range(
        self,
        after_date: datetime,
        before_date: datetime,
        has_attachment: bool = True,
    ) -> list[int]:
        """Search for emails in a date range.

        Args:
            after_date: Start of date range.
            before_date: End of date range.
            has_attachment: If True, only return emails with attachments.

        Returns:
            List of message UIDs.
        """
        criteria = SearchCriteria(
            has_attachment=has_attachment,
            after_date=after_date,
            before_date=before_date,
        )
        return self.search(criteria)

    def get_all_with_attachments(self) -> list[int]:
        """Get all emails with attachments.

        Returns:
            List of all message UIDs with attachments.
        """
        return self.search_raw("has:attachment")


def parse_size_string(size_str: str) -> int:
    """Parse human-readable size string to bytes.

    Args:
        size_str: Size string like "5MB", "100KB", "1G".

    Returns:
        Size in bytes.

    Raises:
        ValueError: If format is invalid.
    """
    size_str = size_str.strip().upper()

    # Handle different suffixes
    multipliers = {
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "M": 1024 * 1024,
        "MB": 1024 * 1024,
        "G": 1024 * 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
    }

    for suffix, multiplier in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if size_str.endswith(suffix):
            number_str = size_str[: -len(suffix)].strip()
            try:
                return int(float(number_str) * multiplier)
            except ValueError:
                raise ValueError(f"Invalid size format: {size_str}") from None

    # No suffix - assume bytes
    try:
        return int(size_str)
    except ValueError:
        raise ValueError(f"Invalid size format: {size_str}") from None


def parse_date_string(date_str: str) -> datetime:
    """Parse date string to datetime.

    Supports formats:
    - YYYY-MM-DD
    - DD-Mon-YYYY
    - relative: "30d", "6m", "1y" (days/months/years ago)

    Args:
        date_str: Date string to parse.

    Returns:
        Parsed datetime.

    Raises:
        ValueError: If format is invalid.
    """
    from datetime import timedelta

    date_str = date_str.strip().lower()

    # Check for relative format
    if date_str.endswith("d"):
        try:
            days = int(date_str[:-1])
            return datetime.now() - timedelta(days=days)
        except ValueError:
            pass

    if date_str.endswith("m"):
        try:
            months = int(date_str[:-1])
            return datetime.now() - timedelta(days=months * 30)
        except ValueError:
            pass

    if date_str.endswith("y"):
        try:
            years = int(date_str[:-1])
            return datetime.now() - timedelta(days=years * 365)
        except ValueError:
            pass

    # Try standard formats
    formats = [
        "%Y-%m-%d",
        "%d-%b-%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    raise ValueError(f"Invalid date format: {date_str}")
