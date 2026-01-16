# Gmail Attachment Stripper with Backup

A Python CLI tool that safely removes attachments from Gmail emails while preserving email integrity, threading, and labels. Attachments are backed up locally before removal. This tool allows cleaning of Google drive storage space with greater control and functionality over Google's native tools.

## Features

- **OAuth2 Authentication** - Secure Gmail access via IMAP with encrypted token storage
- **Smart Search** - Filter emails by size, date, sender, and labels using Gmail search syntax
- **Safe Extraction** - Download attachments to organized backup directory with SHA-256 verification
- **Preserve Threading** - Maintains Message-ID, References, and In-Reply-To headers
- **Label Preservation** - Restores Gmail labels after email replacement
- **Two-Phase Replace** - Upload verified replacement before deleting original
- **Transaction Logging** - Recovery from interruptions with JSONL transaction logs
- **Dry Run Mode** - Preview changes before making modifications
- **Revert Capability** - Restore original emails from Trash within 30 days if needed

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

This tool uses OAuth2 to access Gmail via IMAP. You need to create credentials in Google Cloud Console.

### Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click the project dropdown (top left, next to "Google Cloud")
3. Click **New Project**
4. Enter a project name (e.g., "Gmail Attachment Stripper")
5. Click **Create**
6. Wait for the project to be created, then select it from the dropdown

### Step 2: Enable the Gmail API

1. In the left sidebar, go to **APIs & Services** > **Library**
2. Search for "Gmail API"
3. Click on **Gmail API** in the results
4. Click **Enable**

### Step 3: Configure OAuth Consent Screen

1. Go to **APIs & Services** > **OAuth consent screen**
2. Select **External** user type (unless you have a Google Workspace organization)
3. Click **Create**
4. Fill in the required fields:
   - **App name**: Gmail Attachment Stripper (or any name)
   - **User support email**: Select your email
   - **Developer contact email**: Enter your email
5. Click **Save and Continue**
6. On the **Scopes** page:
   - Click **Add or Remove Scopes**
   - In the filter box, search for `https://mail.google.com/`
   - Check the box for `https://mail.google.com/` (Gmail full access)
   - Click **Update**
7. Click **Save and Continue**
8. On the **Test users** page:
   - Click **Add Users**
   - Enter your Gmail address (the one you'll use with this tool)
   - Click **Add**
9. Click **Save and Continue**
10. Review and click **Back to Dashboard**

> **Note**: Your app will stay in "Testing" mode, which is fine for personal use. Only the test users you added can authenticate. If you skip adding test users, authentication will fail.

### Step 4: Create OAuth Credentials

1. Go to **APIs & Services** > **Credentials**
2. Click **Create Credentials** > **OAuth client ID**
3. Select **Desktop app** as the application type
4. Enter a name (e.g., "Gmail Stripper Desktop")
5. Click **Create**
6. A dialog appears with your credentials - click **Download JSON**
7. Save the file as `credentials.json` in your project directory

### Step 5: Authenticate

Run the auth command to complete OAuth setup:

```bash
gmail-clean auth --credentials credentials.json --email your-email@gmail.com
```

This will:
1. Open your browser to Google's login page
2. Ask you to sign in and grant permissions
3. Save an encrypted token locally (`token.enc`)

> **Token Expiry**: Access tokens expire after 1 hour. The tool automatically refreshes them using the refresh token. If you get authentication errors after some time, just run the `auth` command again.

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

# Process and create zip archives for each file type
gmail-clean process --email your-email@gmail.com --min-size 1MB --no-dry-run --zip
```

### Check Status

```bash
gmail-clean status
```

### Revert Processed Emails

If you need to restore original emails (with attachments) after processing, you can revert them while the originals are still in Gmail Trash (typically 30 days).

```bash
# List emails that can be reverted
gmail-clean revert --email your-email@gmail.com --list

# Preview what would be reverted (dry run)
gmail-clean revert --email your-email@gmail.com

# Revert all revertible emails
gmail-clean revert --email your-email@gmail.com --no-dry-run

# Revert a specific email by its ID
gmail-clean revert --email your-email@gmail.com --id 1234567890 --no-dry-run
```

**How revert works:**
1. Finds the original email in Gmail Trash using the Message-ID
2. Copies the original back to All Mail
3. Restores the original labels
4. Deletes the stripped version
5. Updates the manifest status to "reverted"

**Important notes:**
- Revert only works while originals are in Trash (default 30 days)
- Once Gmail permanently deletes from Trash, revert is not possible
- Attachments remain in your local backup even after revert

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
  organize_by: "type"  # type (default), date, sender, or label

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

By default, attachments are organized by file type with date-prefixed filenames for easy browsing and sorting:

```
backups/
├── images/
│   ├── 2005-04-09_vacation_photo.jpg
│   ├── 2008-06-15_birthday.png
│   └── 2010-12-25_christmas.heic
├── documents/
│   ├── 2006-03-20_report.pdf
│   ├── 2009-11-10_spreadsheet.xlsx
│   └── 2012-07-04_presentation.pptx
├── audio/
│   └── 2007-08-30_voicemail.mp3
├── video/
│   └── 2011-05-22_clip.mp4
└── other/
    └── 2013-02-14_archive.zip
```

**File type categories:**
- **images**: jpg, jpeg, png, gif, bmp, webp, heic, heif, tiff, svg, raw, cr2, nef, psd
- **documents**: pdf, doc, docx, xls, xlsx, ppt, pptx, txt, rtf, odt, csv, md, html
- **audio**: mp3, wav, m4a, flac, ogg, aac, wma, aiff, mid, midi
- **video**: mp4, mov, avi, mkv, wmv, flv, webm, m4v, mpeg, mpg
- **other**: everything else

### Alternative Organization Strategies

You can change `organize_by` in your config to use different structures:

- **`type`** (default): Flat folders by file type with date-prefixed filenames
- **`date`**: Nested year/month/day/subject folders
- **`sender`**: Organized by sender domain and email
- **`label`**: Organized by Gmail label

### Creating Zip Archives

Use the `--zip` flag with the process command to create zip archives for each file type category:

```bash
gmail-clean process --email your-email@gmail.com --no-dry-run --zip
```

This creates:
```
backups/
├── images.zip
├── documents.zip
├── audio.zip
├── video.zip
└── other.zip
```

A manifest file (`manifest.json`) tracks all processed emails and their backup locations.

## Safety Features

- **Dry Run by Default** - Always preview before making changes
- **Two-Phase Replace** - New email uploaded and verified before original is deleted
- **Trash Retention** - Originals moved to Trash, not permanently deleted
- **Revert Command** - Restore originals from Trash within 30 days if needed
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
Backup Location: backups/documents/2024-01-15_document.pdf
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
