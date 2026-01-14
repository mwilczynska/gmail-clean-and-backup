"""CLI commands using Typer."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from src.cli.config import Config, load_config, validate_config
from src.cli.output import RichOutput

app = typer.Typer(
    name="gmail-clean",
    help="Gmail Attachment Stripper with Backup - safely remove attachments while preserving email integrity.",
    add_completion=False,
)
console = Console()
output = RichOutput(console)


def get_config(config_path: Optional[Path]) -> Config:
    """Load and validate configuration.

    Args:
        config_path: Optional path to config file.

    Returns:
        Validated Config object.

    Raises:
        typer.Exit: If configuration is invalid.
    """
    config = load_config(config_path)
    issues = validate_config(config)

    if issues:
        for issue in issues:
            output.print_warning(issue)

    return config


@app.command()
def auth(
    credentials: Path = typer.Option(
        ...,
        "--credentials",
        "-c",
        help="Path to OAuth credentials JSON from Google Cloud Console",
    ),
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Gmail address to authenticate",
    ),
    token_file: Path = typer.Option(
        Path("token.enc"),
        "--token",
        "-t",
        help="Path to store encrypted OAuth token",
    ),
) -> None:
    """Authenticate with Gmail via OAuth2.

    This will open a browser window for Google authentication.
    After successful authentication, the token is encrypted and stored locally.
    """
    from src.auth.oauth import AuthenticationError, GmailOAuth

    output.console.print(f"Authenticating [cyan]{email}[/cyan]...")

    try:
        oauth = GmailOAuth(
            credentials_file=credentials,
            token_file=token_file,
        )

        # Run OAuth flow
        creds = oauth.get_credentials()

        if creds and creds.valid:
            output.print_success(f"Authentication successful for {email}")
            output.console.print(f"Token saved to: {token_file}")
        else:
            output.print_error("Authentication returned invalid credentials")
            raise typer.Exit(1)

    except AuthenticationError as e:
        output.print_error("Authentication failed", str(e))
        raise typer.Exit(1)


@app.command()
def scan(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Gmail address to scan",
    ),
    min_size: str = typer.Option(
        "100KB",
        "--min-size",
        "-s",
        help="Minimum attachment size (e.g., 100KB, 1MB, 5M)",
    ),
    before: Optional[str] = typer.Option(
        None,
        "--before",
        "-b",
        help="Emails before date (YYYY-MM-DD or relative: 30d, 6m, 1y)",
    ),
    after: Optional[str] = typer.Option(
        None,
        "--after",
        "-a",
        help="Emails after date (YYYY-MM-DD or relative)",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        help="Path to config file",
    ),
    export: Optional[Path] = typer.Option(
        None,
        "--export",
        help="Export results to CSV file",
    ),
    limit: int = typer.Option(
        100,
        "--limit",
        "-l",
        help="Maximum emails to scan",
    ),
) -> None:
    """Scan mailbox for emails with attachments.

    Shows statistics about emails that can be processed.
    """
    from src.auth.oauth import GmailOAuth
    from src.imap.client import GmailIMAPClient
    from src.imap.scanner import EmailScanner
    from src.imap.search import GmailSearcher, SearchCriteria, parse_date_string, parse_size_string

    config = get_config(config_path)

    # Parse size
    try:
        min_size_bytes = parse_size_string(min_size)
    except ValueError as e:
        output.print_error(f"Invalid size format: {min_size}", str(e))
        raise typer.Exit(1)

    # Parse dates
    before_date = None
    after_date = None

    if before:
        try:
            before_date = parse_date_string(before)
        except ValueError as e:
            output.print_error(f"Invalid date format: {before}", str(e))
            raise typer.Exit(1)

    if after:
        try:
            after_date = parse_date_string(after)
        except ValueError as e:
            output.print_error(f"Invalid date format: {after}", str(e))
            raise typer.Exit(1)

    output.console.print(f"Scanning [cyan]{email}[/cyan]...")
    output.console.print(f"  Min size: {min_size}")
    if before_date:
        output.console.print(f"  Before: {before_date.strftime('%Y-%m-%d')}")
    if after_date:
        output.console.print(f"  After: {after_date.strftime('%Y-%m-%d')}")

    try:
        # Connect to Gmail
        oauth = GmailOAuth(
            credentials_file=config.oauth.credentials_file,
            token_file=config.oauth.token_file,
        )

        with GmailIMAPClient(oauth, email) as client:
            client.select_folder("[Gmail]/All Mail", readonly=True)

            # Search for emails
            searcher = GmailSearcher(client)
            criteria = SearchCriteria(
                has_attachment=True,
                min_size=min_size_bytes,
                before_date=before_date,
                after_date=after_date,
            )

            output.console.print("\nSearching for emails...")
            uids = searcher.search(criteria)

            if not uids:
                output.console.print("[yellow]No matching emails found.[/yellow]")
                raise typer.Exit(0)

            output.console.print(f"Found {len(uids)} matching emails")

            # Limit results
            if len(uids) > limit:
                output.console.print(f"Limiting scan to first {limit} emails")
                uids = uids[:limit]

            # Scan emails
            scanner = EmailScanner(client)

            with output.create_progress_bar() as progress:
                task = progress.add_task("Scanning emails...", total=len(uids))

                results = []
                for uid in uids:
                    result = scanner.scan_email(uid)
                    results.append(result)
                    progress.update(task, advance=1)

            # Generate and display statistics
            stats = scanner.generate_statistics(results)

            output.console.print()
            output.print_statistics(stats)
            output.console.print()
            output.print_scan_results(results)

            # Export if requested
            if export:
                _export_scan_results(results, export)
                output.print_success(f"Results exported to {export}")

    except Exception as e:
        output.print_error("Scan failed", str(e))
        raise typer.Exit(1)


@app.command()
def process(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Gmail address to process",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Preview changes without making modifications",
    ),
    batch_size: int = typer.Option(
        50,
        "--batch-size",
        "-b",
        help="Number of emails per batch",
    ),
    min_size: str = typer.Option(
        "100KB",
        "--min-size",
        "-s",
        help="Minimum attachment size",
    ),
    before: Optional[str] = typer.Option(
        None,
        "--before",
        help="Process emails before date",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        help="Path to config file",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompts",
    ),
) -> None:
    """Process emails: extract attachments and strip from messages.

    By default runs in dry-run mode. Use --no-dry-run to make actual changes.
    """
    from src.auth.oauth import GmailOAuth
    from src.imap.client import GmailIMAPClient
    from src.imap.scanner import EmailScanner
    from src.imap.search import GmailSearcher, SearchCriteria, parse_date_string, parse_size_string
    from src.processor.backup import BackupManager
    from src.processor.batch import BatchPreview, BatchProcessor
    from src.processor.transaction import TransactionManager
    from src.utils.logging import OperationLogger
    from src.utils.manifest import ManifestManager

    config = get_config(config_path)

    # Parse parameters
    try:
        min_size_bytes = parse_size_string(min_size)
    except ValueError as e:
        output.print_error(f"Invalid size: {min_size}", str(e))
        raise typer.Exit(1)

    before_date = None
    if before:
        try:
            before_date = parse_date_string(before)
        except ValueError as e:
            output.print_error(f"Invalid date: {before}", str(e))
            raise typer.Exit(1)

    mode = "[yellow]DRY RUN[/yellow]" if dry_run else "[red]LIVE MODE[/red]"
    output.console.print(f"\nProcessing mode: {mode}")

    if not dry_run and not yes:
        output.print_warning(
            "This will modify emails in your Gmail account. "
            "Attachments will be backed up locally before removal."
        )
        if not output.confirm("Continue?"):
            raise typer.Exit(0)

    try:
        oauth = GmailOAuth(
            credentials_file=config.oauth.credentials_file,
            token_file=config.oauth.token_file,
        )

        with GmailIMAPClient(oauth, email) as client:
            client.select_folder("[Gmail]/All Mail", readonly=dry_run)

            # Search
            searcher = GmailSearcher(client)
            criteria = SearchCriteria(
                has_attachment=True,
                min_size=min_size_bytes,
                before_date=before_date,
            )

            output.console.print("\nSearching for emails...")
            uids = searcher.search(criteria)

            if not uids:
                output.console.print("[yellow]No matching emails found.[/yellow]")
                raise typer.Exit(0)

            # Scan
            scanner = EmailScanner(client)
            output.console.print(f"Scanning {len(uids)} emails...")

            results = []
            for uid in uids[:batch_size]:
                results.append(scanner.scan_email(uid))

            # Preview
            preview = BatchPreview(results)
            summary = preview.generate_summary()

            output.console.print()
            output.print_batch_preview(summary)

            if dry_run:
                output.console.print(
                    "\n[yellow]Dry run complete. Use --no-dry-run to make actual changes.[/yellow]"
                )
                raise typer.Exit(0)

            # Confirm before processing
            if not yes:
                if not output.confirm(f"\nProcess {summary['processable']} emails?"):
                    raise typer.Exit(0)

            # Setup managers
            backup_manager = BackupManager(config.backup.directory)
            manifest_manager = ManifestManager(Path("manifest.json"))
            txn_manager = TransactionManager(config.safety.transaction_log)
            op_logger = OperationLogger(Path("logs/operations.jsonl"))

            # Process
            processor = BatchProcessor(
                client=client,
                backup_manager=backup_manager,
                manifest_manager=manifest_manager,
                transaction_manager=txn_manager,
                operation_logger=op_logger,
            )

            with output.create_progress_bar() as progress:
                task = progress.add_task("Processing...", total=len(results))

                def progress_callback(current: int, total: int, message: str) -> None:
                    progress.update(task, completed=current, description=message[:50])

                batch_result = processor.process_batch(
                    results, dry_run=False, progress_callback=progress_callback
                )

            output.console.print()
            output.print_batch_result(batch_result)

    except Exception as e:
        output.print_error("Processing failed", str(e))
        raise typer.Exit(1)


@app.command()
def status(
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        help="Path to config file",
    ),
) -> None:
    """Show processing status and statistics."""
    from src.utils.manifest import ManifestManager

    manifest_path = Path("manifest.json")

    if not manifest_path.exists():
        output.console.print("[yellow]No manifest found. Run 'process' first.[/yellow]")
        raise typer.Exit(0)

    manifest = ManifestManager(manifest_path)
    stats = manifest.get_processing_stats()

    output.console.print("\n[bold]Processing Status[/bold]")
    output.console.print(f"  Total emails processed: {stats['total']}")

    output.console.print("\n[bold]By Status:[/bold]")
    for status_name, count in stats["by_status"].items():
        output.console.print(f"  {status_name}: {count}")

    if stats["total_savings"] > 0:
        savings_mb = stats["total_savings"] / (1024 * 1024)
        output.console.print(f"\n[bold]Total Storage Saved:[/bold] [green]{savings_mb:.1f} MB[/green]")


@app.command()
def export_manifest(
    output_path: Path = typer.Argument(
        ...,
        help="Output file path",
    ),
    format: str = typer.Option(
        "json",
        "--format",
        "-f",
        help="Export format: json or csv",
    ),
) -> None:
    """Export processing manifest to file."""
    from src.utils.manifest import ManifestManager

    manifest_path = Path("manifest.json")

    if not manifest_path.exists():
        output.print_error("No manifest found")
        raise typer.Exit(1)

    manifest = ManifestManager(manifest_path)
    manifest.export_manifest(output_path, format)

    output.print_success(f"Manifest exported to {output_path}")


@app.command()
def cleanup(
    older_than_days: int = typer.Option(
        30,
        "--older-than-days",
        "-d",
        help="Delete trash older than N days",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation",
    ),
) -> None:
    """Cleanup old transaction logs and empty backup directories."""
    from src.processor.backup import BackupManager
    from src.processor.transaction import TransactionManager

    config = load_config(None)

    # Cleanup transaction logs
    txn_manager = TransactionManager(config.safety.transaction_log)
    removed_logs = txn_manager.cleanup_old_logs(older_than_days)
    output.console.print(f"Removed {removed_logs} old transaction log entries")

    # Cleanup empty backup directories
    backup_manager = BackupManager(config.backup.directory)
    removed_dirs = backup_manager.cleanup_empty_dirs()
    output.console.print(f"Removed {removed_dirs} empty backup directories")

    output.print_success("Cleanup complete")


def _export_scan_results(results: list, path: Path) -> None:
    """Export scan results to CSV.

    Args:
        results: Scan results.
        path: Output path.
    """
    import csv

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "UID", "Date", "From", "Subject", "Attachments",
            "Total Size", "Strippable Size", "Encrypted", "Labels"
        ])

        for result in results:
            writer.writerow([
                result.header.uid,
                result.header.date.isoformat(),
                result.header.sender,
                result.header.subject,
                len(result.attachments),
                result.total_attachment_size,
                result.strippable_size,
                result.is_encrypted,
                ";".join(result.gmail_metadata.labels),
            ])


if __name__ == "__main__":
    app()
