"""Rich console output formatting."""

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from src.models.email import BatchResult, EmailScanResult, ScanStatistics


class RichOutput:
    """Rich console output formatting."""

    def __init__(self, console: Console | None = None) -> None:
        """Initialize with Rich console.

        Args:
            console: Rich Console instance.
        """
        self.console = console or Console()

    def print_scan_results(
        self,
        results: list[EmailScanResult],
        limit: int = 20,
    ) -> None:
        """Display scan results as formatted table.

        Args:
            results: Scan results to display.
            limit: Maximum rows to show.
        """
        table = Table(title="Email Scan Results", show_lines=True)

        table.add_column("Date", style="cyan", width=12)
        table.add_column("From", style="green", max_width=30)
        table.add_column("Subject", max_width=40)
        table.add_column("Attachments", justify="right")
        table.add_column("Size", justify="right", style="yellow")
        table.add_column("Status", style="magenta")

        for result in results[:limit]:
            date_str = result.header.date.strftime("%Y-%m-%d")
            sender = self._truncate(result.header.sender, 30)
            subject = self._truncate(result.header.subject, 40)
            att_count = len(result.strippable_attachments)
            size = self._format_size(result.strippable_size)

            status = "OK"
            if result.is_encrypted:
                status = "[red]Encrypted[/red]"
            elif not result.strippable_attachments:
                status = "[dim]Inline only[/dim]"

            table.add_row(date_str, sender, subject, str(att_count), size, status)

        self.console.print(table)

        if len(results) > limit:
            self.console.print(
                f"\n[dim]... and {len(results) - limit} more emails[/dim]"
            )

    def print_statistics(self, stats: ScanStatistics) -> None:
        """Display statistics with formatted output.

        Args:
            stats: Scan statistics.
        """
        panel_content = f"""
[bold]Total Emails:[/bold] {stats.total_emails}
[bold]Total Attachments:[/bold] {stats.total_attachments}
[bold]Estimated Savings:[/bold] [green]{stats.estimated_savings_human}[/green]

[bold]Processable:[/bold] {stats.processable_emails}
[bold]Encrypted (skipped):[/bold] {stats.encrypted_emails_skipped}
[bold]Inline-only (skipped):[/bold] {stats.emails_with_inline_only}
"""
        self.console.print(Panel(panel_content, title="Scan Statistics"))

        # By year breakdown
        if stats.by_year:
            self.console.print("\n[bold]Emails by Year:[/bold]")
            for year, count in sorted(stats.by_year.items()):
                bar = "=" * min(count, 50)
                self.console.print(f"  {year}: {bar} ({count})")

        # Top content types
        if stats.by_content_type:
            self.console.print("\n[bold]Top Attachment Types:[/bold]")
            sorted_types = sorted(
                stats.by_content_type.items(), key=lambda x: -x[1]
            )[:10]
            for content_type, count in sorted_types:
                self.console.print(f"  {content_type}: {count}")

    def print_batch_preview(self, summary: dict[str, Any]) -> None:
        """Display batch processing preview.

        Args:
            summary: Preview summary from BatchPreview.
        """
        panel_content = f"""
[bold]Emails to Process:[/bold] {summary['processable']}
[bold]Total Attachments:[/bold] {summary['total_attachments']}
[bold]Estimated Savings:[/bold] [green]{summary['estimated_savings_human']}[/green]

[dim]Encrypted emails skipped: {summary['encrypted_skipped']}[/dim]
[dim]Inline-only skipped: {summary['inline_only_skipped']}[/dim]
"""
        self.console.print(Panel(panel_content, title="Processing Preview"))

    def print_batch_result(self, result: BatchResult) -> None:
        """Display batch processing result.

        Args:
            result: Batch processing result.
        """
        status_color = "green" if result.failed == 0 else "yellow"

        panel_content = f"""
[bold]Processed:[/bold] {result.total_processed}
[bold]Successful:[/bold] [{status_color}]{result.successful}[/{status_color}]
[bold]Failed:[/bold] [red]{result.failed}[/red]
[bold]Skipped:[/bold] {result.skipped}

[bold]Storage Saved:[/bold] [green]{result.bytes_saved_human}[/green]
[bold]Duration:[/bold] {result.duration_seconds:.1f} seconds
"""
        self.console.print(Panel(panel_content, title="Processing Complete"))

        if result.errors:
            self.console.print("\n[bold red]Errors:[/bold red]")
            for error in result.errors[:10]:
                self.console.print(f"  - {error.get('email_id')}: {error.get('error')}")

    def print_progress(
        self,
        current: int,
        total: int,
        message: str,
    ) -> None:
        """Display progress update.

        Args:
            current: Current item number.
            total: Total items.
            message: Progress message.
        """
        pct = (current / total * 100) if total > 0 else 0
        self.console.print(f"[{current}/{total}] ({pct:.0f}%) {message}")

    def print_error(self, message: str, details: str | None = None) -> None:
        """Display error message.

        Args:
            message: Error message.
            details: Optional additional details.
        """
        self.console.print(f"[bold red]Error:[/bold red] {message}")
        if details:
            self.console.print(f"[dim]{details}[/dim]")

    def print_success(self, message: str) -> None:
        """Display success message.

        Args:
            message: Success message.
        """
        self.console.print(f"[bold green]Success:[/bold green] {message}")

    def print_warning(self, message: str) -> None:
        """Display warning message.

        Args:
            message: Warning message.
        """
        self.console.print(f"[bold yellow]Warning:[/bold yellow] {message}")

    def confirm(self, message: str) -> bool:
        """Request user confirmation.

        Args:
            message: Confirmation prompt.

        Returns:
            True if user confirms.
        """
        response = self.console.input(f"{message} [y/N]: ")
        return response.lower() in ("y", "yes")

    def create_progress_bar(self) -> Progress:
        """Create a Rich progress bar.

        Returns:
            Progress instance.
        """
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=self.console,
        )

    def _truncate(self, text: str, max_length: int) -> str:
        """Truncate text with ellipsis.

        Args:
            text: Text to truncate.
            max_length: Maximum length.

        Returns:
            Truncated text.
        """
        if len(text) <= max_length:
            return text
        return text[: max_length - 3] + "..."

    def _format_size(self, size: int) -> str:
        """Format size in human-readable form.

        Args:
            size: Size in bytes.

        Returns:
            Human-readable size string.
        """
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.2f} GB"
