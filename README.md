# Gmail Attachment Stripper with Backup

A Python CLI tool that safely removes attachments from Gmail emails while preserving email integrity, threading, and labels. Attachments are backed up locally before removal.

## Features

- **OAuth2 Authentication** - Secure Gmail access via IMAP with encrypted token storage
- **Smart Search** - Filter emails by size, date, sender, and labels using Gmail search syntax
- **Safe Extraction** - Download attachments to organized backup directory with SHA-256 verification
- **Preserve Threading** - Maintains Message-ID, References, and In-Reply-To headers
- **Label Preservation** - Restores Gmail labels after email replacement
- **Two-Phase Replace** - Upload verified replacement before deleting original
- **Transaction Logging** - Recovery from interruptions with JSONL transaction logs
- **Dry Run Mode** - Preview changes before making modifications

## Installation

### Prerequisites

- Python 3.10 or higher
- A Google Cloud project with Gmail API enabled

### Install from source

```bash
git clone https://github.com/mwilczynska/gmail-clean-and-backup.git
cd gmail-clean-and-backup
pip install -e .
```

Or install dependencies directly:

```bash
pip install -r requirements.txt
```

## Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the **Gmail API** (APIs & Services > Library > search "Gmail API")
4. Configure the **OAuth consent screen**:
   - Choose "External" user type
   - Add your email as a test user
   - Add scope: `https://mail.google.com/`
5. Create **OAuth credentials**:
   - APIs & Services > Credentials > Create Credentials > OAuth client ID
   - Application type: Desktop app
   - Download the JSON file and save as `credentials.json`

## Usage

### Authenticate

```bash
gmail-clean auth --credentials credentials.json --email your-email@gmail.com
```

This opens a browser for Google authentication. The token is encrypted and stored locally.

### Scan for Emails with Attachments

```bash
# Basic scan
gmail-clean scan --email your-email@gmail.com

# Filter by size and date
gmail-clean scan --email your-email@gmail.com --min-size 5MB --before 2020-01-01

# Export results to CSV
gmail-clean scan --email your-email@gmail.com --export scan_results.csv
```

### Process Emails (Strip Attachments)

```bash
# Dry run (preview only - default)
gmail-clean process --email your-email@gmail.com --min-size 1MB

# Actually process emails
gmail-clean process --email your-email@gmail.com --min-size 1MB --no-dry-run

# Process with specific date range
gmail-clean process --email your-email@gmail.com --before 2015-01-01 --min-size 10MB --no-dry-run
```

### Check Status

```bash
gmail-clean status
```

### Export Manifest

```bash
gmail-clean export-manifest manifest.json
gmail-clean export-manifest manifest.csv --format csv
```

### Cleanup

```bash
gmail-clean cleanup --older-than-days 30
```

## Configuration

Copy `config.example.yaml` to `config.yaml` and customize:

```yaml
gmail:
  email: "your-email@gmail.com"

oauth:
  credentials_file: "credentials.json"
  token_file: "token.enc"

backup:
  directory: "./backups"
  organize_by: "date"  # date, sender, or label

processing:
  dry_run: true
  batch_size: 50
  skip_encrypted: true
  preserve_inline_images: true
  min_attachment_size: 102400  # 100KB

safety:
  keep_trash_days: 30
  require_confirmation: true
```

## Backup Structure

Attachments are organized by date by default:

```
backups/
└── 2024/
    └── 01/
        └── 15/
            └── Meeting_Notes/
                ├── presentation.pdf
                └── spreadsheet.xlsx
```

A manifest file (`manifest.json`) tracks all processed emails and their backup locations.

## Safety Features

- **Dry Run by Default** - Always preview before making changes
- **Two-Phase Replace** - New email uploaded and verified before original is deleted
- **Trash Retention** - Originals moved to Trash, not permanently deleted
- **Transaction Logging** - Operations logged for recovery from interruptions
- **Encrypted Emails Skipped** - S/MIME and PGP emails are detected and skipped
- **Inline Images Preserved** - Only strips actual attachments, not embedded images

## How It Works

1. **Scan**: Query Gmail for emails with attachments matching your criteria
2. **Extract**: Download attachments to local backup directory
3. **Reconstruct**: Build new email without attachments, adding placeholder text
4. **Replace**: Upload reconstructed email, verify, apply labels, delete original

The placeholder in the email body shows:
```
[Attachment Removed]
Filename: document.pdf
Original Size: 2.5 MB
Backup Location: backups/2024/01/15/Meeting_Notes/document.pdf
```

## Limitations

- Cannot process encrypted emails (S/MIME, PGP)
- Requires full Gmail API scope (`https://mail.google.com/`)
- Processing speed limited by Gmail rate limits (~5-10 seconds per email)

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type checking
mypy src/

# Linting
ruff check src/
```

## License

MIT License - see [LICENSE](LICENSE) file.

## Contributing

Contributions welcome! Please open an issue to discuss changes before submitting a PR.

## Disclaimer

This tool modifies emails in your Gmail account. While it includes safety features, always:
- Start with dry-run mode
- Test on a small batch first
- Verify backups are created correctly
- Keep originals in Trash until you're confident
