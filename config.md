# VaultSync Configuration

VaultSync connects Obsidian/Markdown vaults to Anki using Markdown2Anki.

## Settings

- `auto_close_on_success`: Automatically close the VaultSync dashboard after a successful sync.
- `show_diff_before_update`: Preview modified cards in a diff dialog before applying changes to Anki.
- `show_status_bar`: Display a minimalist status indicator in the bottom-right corner of Anki's main status bar.
- `sync_on_startup`: Automatically run a background sync when opening Anki.
- `backup_before_sync`: Automatically create an Anki collection backup before every synchronization.
- `shortcuts`: Global keyboard shortcuts for VaultSync operations (`open_dashboard`, `quick_sync`, `full_sync`).
- `profiles`: Per-Anki-profile settings.
  - `vault_path`: Path to the active Obsidian vault directory.
  - `occlusion_dir`: Output directory for staged Image Occlusion photos.
