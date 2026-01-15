"""Configuration loading and validation."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class GmailConfig:
    """Gmail account configuration."""

    email: str = ""


@dataclass
class OAuthConfig:
    """OAuth configuration."""

    credentials_file: Path = Path("credentials.json")
    token_file: Path = Path("token.enc")
    scopes: list[str] = field(default_factory=lambda: ["https://mail.google.com/"])


@dataclass
class BackupConfig:
    """Backup storage configuration."""

    directory: Path = Path("./backups")
    organize_by: str = "type"  # type (default), date, sender, label


@dataclass
class ProcessingConfig:
    """Processing options."""

    dry_run: bool = True
    batch_size: int = 50
    skip_encrypted: bool = True
    preserve_inline_images: bool = True
    min_attachment_size: int = 102400  # 100KB


@dataclass
class SafetyConfig:
    """Safety options."""

    keep_trash_days: int = 30
    require_confirmation: bool = True
    transaction_log: Path = Path("./logs/transactions.jsonl")


@dataclass
class SearchConfig:
    """Default search options."""

    before_date: str | None = None
    after_date: str | None = None
    from_senders: str | None = None
    labels: str | None = None
    exclude_labels: str | None = None


@dataclass
class Config:
    """Complete application configuration."""

    gmail: GmailConfig = field(default_factory=GmailConfig)
    oauth: OAuthConfig = field(default_factory=OAuthConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    search: SearchConfig = field(default_factory=SearchConfig)


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration from file.

    Args:
        config_path: Path to YAML config file. Defaults to config.yaml.

    Returns:
        Loaded Config object.
    """
    config = Config()

    # Try default paths if not specified
    if config_path is None:
        for default_path in ["config.yaml", "config.yml", "config.local.yaml"]:
            if Path(default_path).exists():
                config_path = Path(default_path)
                break

    if config_path and config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            config = _parse_config(data)

    return config


def _parse_config(data: dict[str, Any]) -> Config:
    """Parse configuration dictionary into Config object.

    Args:
        data: Raw configuration dictionary.

    Returns:
        Config object.
    """
    config = Config()

    # Gmail section
    if "gmail" in data:
        gmail_data = data["gmail"]
        config.gmail = GmailConfig(
            email=gmail_data.get("email", ""),
        )

    # OAuth section
    if "oauth" in data:
        oauth_data = data["oauth"]
        config.oauth = OAuthConfig(
            credentials_file=Path(oauth_data.get("credentials_file", "credentials.json")),
            token_file=Path(oauth_data.get("token_file", "token.enc")),
            scopes=oauth_data.get("scopes", ["https://mail.google.com/"]),
        )

    # Backup section
    if "backup" in data:
        backup_data = data["backup"]
        config.backup = BackupConfig(
            directory=Path(backup_data.get("directory", "./backups")),
            organize_by=backup_data.get("organize_by", "type"),
        )

    # Processing section
    if "processing" in data:
        proc_data = data["processing"]
        config.processing = ProcessingConfig(
            dry_run=proc_data.get("dry_run", True),
            batch_size=proc_data.get("batch_size", 50),
            skip_encrypted=proc_data.get("skip_encrypted", True),
            preserve_inline_images=proc_data.get("preserve_inline_images", True),
            min_attachment_size=proc_data.get("min_attachment_size", 102400),
        )

    # Safety section
    if "safety" in data:
        safety_data = data["safety"]
        config.safety = SafetyConfig(
            keep_trash_days=safety_data.get("keep_trash_days", 30),
            require_confirmation=safety_data.get("require_confirmation", True),
            transaction_log=Path(safety_data.get("transaction_log", "./logs/transactions.jsonl")),
        )

    # Search section
    if "search" in data:
        search_data = data["search"]
        config.search = SearchConfig(
            before_date=search_data.get("before_date"),
            after_date=search_data.get("after_date"),
            from_senders=search_data.get("from_senders"),
            labels=search_data.get("labels"),
            exclude_labels=search_data.get("exclude_labels"),
        )

    return config


def validate_config(config: Config) -> list[str]:
    """Validate configuration, return list of issues.

    Args:
        config: Configuration to validate.

    Returns:
        List of validation issue messages.
    """
    issues = []

    # Check email configured
    if not config.gmail.email:
        issues.append("Gmail email address not configured")

    # Check credentials file exists
    if not config.oauth.credentials_file.exists():
        issues.append(f"OAuth credentials file not found: {config.oauth.credentials_file}")

    # Check backup directory is writable
    try:
        config.backup.directory.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        issues.append(f"Cannot create backup directory: {config.backup.directory}")

    # Validate organize_by option
    valid_organize = {"type", "date", "sender", "label"}
    if config.backup.organize_by not in valid_organize:
        issues.append(f"Invalid organize_by value: {config.backup.organize_by}")

    # Validate min_attachment_size
    if config.processing.min_attachment_size < 0:
        issues.append("min_attachment_size cannot be negative")

    return issues


def create_default_config(path: Path) -> None:
    """Create default configuration file.

    Args:
        path: Path to write config file.
    """
    default_config = """# Gmail Attachment Stripper Configuration
# Copy this file and update with your settings

gmail:
  email: "your-email@gmail.com"

oauth:
  credentials_file: "credentials.json"
  token_file: "token.enc"
  scopes:
    - "https://mail.google.com/"

backup:
  directory: "./backups"
  # Organization strategy:
  #   type   - Flat folders by file type with date-prefixed filenames (default)
  #            e.g., backups/images/2005-04-09_photo.jpg
  #   date   - Nested year/month/day/subject folders
  #   sender - Organized by sender domain/email
  #   label  - Organized by Gmail label
  organize_by: "type"

processing:
  dry_run: true
  batch_size: 50
  skip_encrypted: true
  preserve_inline_images: true
  min_attachment_size: 102400

safety:
  keep_trash_days: 30
  require_confirmation: true
  transaction_log: "./logs/transactions.jsonl"
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(default_config)
