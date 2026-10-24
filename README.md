# Anki-VaultSync

**Anki-VaultSync** is a native Anki Desktop add-on that seamlessly synchronizes flashcard notes written in Obsidian or standard Markdown directly with your Anki collection.

It embeds the pure-Python core engine of [**Markdown2Anki**](https://github.com/leon-3/Markdown2Anki) directly inside the Anki desktop process—requiring **no AnkiConnect, no local HTTP servers, and no command-line tools**.

---

## Key Features

- **Direct In-Process Integration**:
  - Interacts directly with Anki's SQLite database (`mw.col`) inside the native Anki desktop process.
  - Changes are applied using atomic database transactions with full Anki Undo support (`Ctrl+Z`).
- **Modern Visual Dashboard**:
  - Metric counters for **New Cards**, **Modified Cards**, **Image Occlusions**, and **Orphaned Cards**.
  - One-click **Sync**, **Preview Diff**, and **Rescan Vault** actions with real-time status indicators.
- **Real-Time Status Bar Indicator**:
  - Toggleable indicator in Anki's main bottom status bar showing pending new and modified cards (`VaultSync: 3 new, 1 mod`).
  - Context menu for instant quick sync, full sync, or opening the dashboard.
- **Interactive Rich Diff Inspection**:
  - Visual HTML diff dialog displaying exact word-level changes (`<ins>` / `<del>`), deck moves, and tag changes before applying edits to Anki.
- **Atomic Flag-Before-Insert Protocol**:
  - Guarantees data integrity by writing unique card IDs (`%%id%%`) to Markdown disk files *before* inserting records into Anki's database, preventing duplicate creation if a crash or power loss occurs.
  - Concurrency-safe: verifies file modification times (`st_mtime_ns`) and SHA-256 hashes to prevent overwriting notes edited externally during sync preparation.
- **In-Place Note Updates & Type Conversion**:
  - Notes are tracked with persistent 6-character IDs (`%%k3f9qz%%` in Obsidian, `<!--m2a:id-->` in Anki).
  - Updating edited flashcards updates note fields in place without resetting review intervals, ease factors, or learning schedules.
  - Seamlessly handles **Note Type Conversions** (e.g. Basic $\leftrightarrow$ Cloze) in-place using Anki's internal schema transition API without losing review history.
- **Undo / Redo Synchronization (`Ctrl+Z` / `Ctrl+Y`)**:
  - Reverting a sync inside Anki automatically clears `ADDED` flags from your Obsidian Markdown files on disk. Redoing a sync restores the flags.
- **Dedicated Deleted Cards Maintenance**:
  - **Orphaned Cards in Anki**: View orphaned cards in Anki's native browser (`nid:...`), suspend cards safely to preserve review history, or delete permanently.
  - **Cards Deleted in Anki**: Detect notes that still have sync IDs in Markdown but were deleted from Anki; mark them as `DELETED:` in Obsidian or re-export them into Anki.
- **Native Image Occlusions**:
  - Creates native Anki 23.10+ / 24+ Image Occlusion notes with `TODO_PROCESS_OCCLUSION` tags, suspending new cards until masks are drawn in Anki.
  - Automatically unsuspends cards and removes the processing tag once masks are drawn.
- **Filtered / Cram Deck Integrity**:
  - Aware of Anki's dynamic filtered deck mechanics: cards studied in dynamic Cram decks retain their true home deck (`odid`), preventing false "deck moved" diff notifications during routine scans.
- **Automatic Collection Backups**:
  - Automatically creates a timestamped Anki collection backup before every synchronization.
- **Custom Keyboard Shortcuts**:
  - Fully configurable global shortcuts for Open Dashboard (`Ctrl+Alt+V`), Quick Sync (`Ctrl+Alt+S`), and Full Sync (`Ctrl+Alt+U`) with real-time conflict detection.

---

## Dashboard Overview

Open the dashboard inside Anki via **Tools $\rightarrow$ VaultSync Dashboard** or shortcut `Ctrl+Alt+V`:

```
┌─────────────────────────────────────────────────────────────────┐
│ Obsidian Vault: /home/leon/Documents/ObsidianVault   [Settings] │
├─────────────────────────────────────────────────────────────────┤
│ ● Ready: 12 new, 3 modified card(s)                             │
├─────────────────────────────────────────────────────────────────┤
│ Card Counts:                                                    │
│  [ 12 ] New Cards       [  3 ] Modified                         │
│  [  1 ] Occlusions      [  0 ] Orphans                          │
├─────────────────────────────────────────────────────────────────┤
│ Sync Options:                                                   │
│  [x] Include Modified Cards (3)   [x] Unflag Completed (1)      │
├─────────────────────────────────────────────────────────────────┤
│ Actions:                                                        │
│  [     Sync (15)     ]  [ Preview Diff (3) ]  [ Rescan Vault ]  │
└─────────────────────────────────────────────────────────────────┘
```

* **Sync**: Writes markdown card IDs to disk, imports new notes, commits field/tag updates, and unsuspends completed occlusions.
* **Preview Diff**: Opens the rich HTML diff dialog to review and selectively approve updates before committing them.
* **Rescan Vault**: Re-reads all Markdown files in the vault and compares them against the current collection.

---

## Comprehensive Settings Guide

The Settings window (`ConfigWindow`) is organized into six specialized tabs:

### 1. General & Sync
* **Vault Location**: Specify the local root directory of your Obsidian or Markdown vault.
* **Ignored Directories & Files**: Glob patterns or directory names to skip during scanning (e.g. `.obsidian`, `templates/*`, `archive`, `ai`).
* **Sync Behavior & Safety**:
  * *Create Collection Backup Before Sync*: Generates an automatic Anki backup prior to mutating notes.
  * *Show Diff Preview Before Updating*: Prompts with the visual diff window prior to modifying existing notes.
  * *Close Dashboard on Successful Sync*: Auto-dismisses the dashboard when synchronization finishes with zero errors.
  * *Auto-sync on Anki Startup (Background)*: Scans and synchronizes notes automatically in the background when Anki launches.
  * *Show Status Bar Indicator*: Toggles the live pending-cards indicator in Anki's main bottom status bar.
* **Occlusion Status in Anki**: Shows notes awaiting hand-drawn masks or text clozes (`TODO_PROCESS_OCCLUSION` and `TODO_PROCESS_CLOZES`) with direct browser shortcuts for image and text occlusions.

### 2. Deck Rules
* **Default Target Deck**: Dropdown selecting the fallback Anki deck for notes that do not match any routing rules.
* **Deck Rules & Folder Mapping**:
  * Interactive table mapping vault folder paths to destination Anki decks.
  * Uses **Longest-Prefix Matching (LPM)**: a rule for `computer_science/algorithms` takes precedence over a rule for `computer_science`.
  * Subfolders automatically inherit the parent folder rule unless explicitly overridden.

### 3. Tag Rules
* **Base Tag Prefix**: Optional root tag prepended before all generated tags (e.g. `vault`).
* **Pad Folder Numbers (01, 02...)**: Formats single-digit folder names with leading zeros (e.g. `'1. Chapter'` $\rightarrow$ `'01_Chapter'`) for natural sorting in Anki's card browser. Alphanumeric terms like `MP3`, `ISO9001`, or `2D Geometry` are automatically protected.
* **Tag Path Aliases**:
  * Map folder paths to custom hierarchical tags (e.g. `"pen and paper"` $\rightarrow$ `"work::writing"`).
  * **Tag Suppression**: Set the replacement to an empty value (`""`) to omit that folder level from generated tags (useful for non-topical folders like `archive` or `general`).
  * Aliased tags are authoritative and exempt from automatic digit padding.

### 4. Card Types & Media
* **Cloze Deletions**:
  * *Convert Cloze Syntax*: Converts `{{c1::...}}` cloze deletion syntax into native Anki Cloze notes.
  * *Suspend Newly Created Cloze Cards*: Suspends new cloze cards until manually reviewed.
* **Image Occlusion Settings**:
  * *Mode*: Choose between **Direct Native Image Occlusions** (recommended for Anki 23.10+) or **Folder Staging** (legacy/manual).
  * *Export Folder*: Destination folder when using staging mode.
  * *Suspend New Occlusions Until Masked*: Suspends new Image Occlusion cards until masks are drawn in Anki.
  * *Re-export Existing Occlusion Images*: Forces re-exporting image occlusions during routine syncs.
  * *Skip Image Occlusion Updates*: Prevents future syncs from overwriting hand-drawn occlusion masks and custom card edits in Anki.
  * *Auto-unflag Completed Occlusions During Sync*: Automatically removes `TODO_PROCESS_OCCLUSION` and unsuspends cards once masks are drawn.
* **Media & Note Type Bindings**:
  * *Unique Image Names (Prevent Overwriting in Anki)*: Renames embedded images and attachments to deterministic 8-character hex hashes (`<8hex>_01.ext`) so different images with identical filenames never overwrite each other in Anki's shared media folder.
  * *Custom Note Type Bindings*: Bind cards to existing custom Anki Note Types instead of the default `M2A Basic` and `M2A Cloze` templates.

### 5. Deleted Cards
* **Orphaned Cards in Anki (Deleted from Obsidian)**:
  * Detects cards in Anki whose source notes were removed from your vault.
  * *View in Anki Browser*: Opens Anki's native browser filtering orphaned notes (`nid:...`).
  * *Suspend All Orphaned Cards*: Suspends cards safely, preserving review intervals, ease factors, and history.
  * *Delete Permanently*: Deletes orphaned notes from the collection after explicit confirmation.
* **Cards Deleted in Anki (Still Active in Obsidian)**:
  * Displays notes in Obsidian with sync IDs that no longer exist in Anki.
  * *Mark Selected as DELETED in Obsidian*: Writes `DELETED: ... %%id%%` to the note lines in your vault.
  * *Resync / Recreate Selected in Anki*: Re-exports selected cards back into Anki as new notes.
### 6. Shortcuts & Logs
* **Keyboard Shortcuts**:
  * Configure custom shortcuts for **Open Dashboard**, **Quick Sync (New)**, and **Full Sync (New + Modified)**.
  * Includes real-time conflict detection against standard Anki shortcuts.
* **Diagnostic Logs**:
  * Live reverse-chronological log viewer showing recent operations, warnings, and error traces.
  * **Open Logs Folder** and **Clear Log** buttons for diagnostic maintenance.

---

## Note Syntax & Conventions

Cards are written in standard Markdown files delimited by horizontal rules (`---`):

````markdown
# Mathematics
## Linear Algebra
---
What is an Eigenvalue?
A scalar $\lambda$ such that $A v = \lambda v$ for a non-zero eigenvector $v$.

---
What is Matrix Rank?
EQL: and how does it relate to invertibility?
The maximum number of linearly independent column vectors. A square matrix is invertible if and only if it has full rank.

---
Cloze
The determinant of an identity matrix is {{c1::1}}.

---
Image Cloze
![[matrix_transformation.png]]

---
How do you implement quicksort in C?
```c
#CODE#
void quicksort(int *arr, int low, int high) { ... }
```
````

---

## Installation & Development Setup

### Symlink Installation (Development)
Symlink this repository into your Anki `addons21` directory:

```bash
# Linux
ln -s "/path/to/Anki-VaultSync" ~/.local/share/Anki2/addons21/anki_vaultsync

# macOS
ln -s "/path/to/Anki-VaultSync" ~/Library/Application\ Support/Anki2/addons21/anki_vaultsync

# Windows (Command Prompt as Administrator)
mklink /D "%APPDATA%\Anki2\addons21\anki_vaultsync" "C:\path\to\Anki-VaultSync"
```

> [!NOTE]
> **Linux Flatpak Users:** If you run Anki via Flatpak (`net.ankiweb.Anki`), grant filesystem access to your home or vault folder:
> ```bash
> flatpak override --user --filesystem=home net.ankiweb.Anki
> ```
> And symlink into `~/.var/app/net.ankiweb.Anki/data/Anki2/addons21/anki_vaultsync`.

### Vendoring the Engine
When parser logic in the sibling repository `Markdown2Anki` changes, sync the vendored package and verify AST imports:

```bash
python3 scripts/bundle.py
```

To build a standalone `.ankiaddon` package archive:
```bash
python3 scripts/bundle.py --package
```

### Running Tests
Execute the automated test suite using pytest:

```bash
pytest tests/
```

> [!NOTE]
> The test suite includes an automated `conftest.py` fixture that safely mocks modal `QMessageBox` popups, guaranteeing non-blocking test execution in headless environments.
