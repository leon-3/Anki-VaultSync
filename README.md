# VaultSync

**VaultSync** is an Anki Desktop add-on providing a native graphical interface for synchronizing Markdown/Obsidian flashcard vaults with Anki.

It uses **Markdown2Anki** as its parsing, rendering, and media processing engine.

## Features

- **Direct In-Process Integration**: Runs natively inside Anki using `mw.col`—no AnkiConnect, local servers, or command line required.
- **Visual Dashboard**: Single-click sync, dry run previews, and real-time activity logs.
- **Flag-Before-Insert Atomicity**: Markdown flags are written and verified on disk before notes are added to Anki, preventing duplicates.
- **High Performance**: Single-pass SQLite indexing locates existing cards without slow full-table scans.
- **Native Undo**: Cleanly integrated with Anki's undo manager (`Ctrl+Z`).
- **Profile Isolation**: Automatically isolates vault paths across different Anki profiles.

## Development Setup

Symlink this repository to your Anki addons directory:

```bash
# Linux
ln -s ~/Coding/anki-vaultsync ~/.local/share/Anki2/addons21/anki-vaultsync

# macOS
ln -s ~/Coding/anki-vaultsync ~/Library/Application\ Support/Anki2/addons21/anki-vaultsync

# Windows (Command Prompt as Administrator)
mklink /D "%APPDATA%\Anki2\addons21\anki-vaultsync" "C:\path\to\anki-vaultsync"
```

To vendor the latest decoupled engine and packaging:

```bash
python scripts/bundle.py
```
