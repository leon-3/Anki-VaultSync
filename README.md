# Anki-VaultSync

**Anki-VaultSync** is a native Anki Desktop add-on providing a visual interface for synchronizing Markdown and Obsidian flashcard vaults directly with your Anki collection.

It embeds the decoupled core engine of [**Markdown2Anki**](https://github.com/leon-3/Markdown2Anki) directly inside the Anki desktop process—requiring **no AnkiConnect, no local HTTP servers, and no command-line tools**.

---

## Key Features

- **Direct In-Process Integration**:
  - Interacts directly with Anki's SQLite database (`mw.col`) inside the native Anki desktop process.
  - Changes are applied using atomic database operations with full Anki Undo support (`Ctrl+Z`).
- **Modern Visual Dashboard**:
  - Live metric counters for **New Cards**, **Modified Cards**, **Image Occlusions**, and **Orphaned Cards**.
  - One-click **Sync**, **Preview Diff**, and **Rescan Vault** actions with real-time status indicators.
- **Interactive Diff Inspection**:
  - Visual HTML diff dialog displaying exact word-level changes (`<ins>` / `<del>`), deck moves, and tag changes before applying edits to Anki.
- **In-Place Note Updates**:
  - Notes are tracked with persistent 6-character IDs (`%%k3f9qz%%` in Obsidian, `<!--m2a:id-->` in Anki).
  - Updating edited flashcards updates note fields in place without resetting review intervals, ease factors, or learning schedules.
- **Orphaned Card Detection**:
  - Automatically identifies flashcards in Anki whose source notes were deleted or moved out of the vault.
  - Dedicated **Find & View Orphaned Cards in Browser** button filters orphaned cards directly in Anki's native Card Browser (`nid:...`).
- **Longest-Prefix Deck Routing**:
  - Declaratively map vault folder trees directly to target Anki decks via an interactive table. More specific subdirectories automatically override parent folder rules.
- **Flexible Tag Aliasing & Suppression**:
  - Replace long folder paths with clean tag abbreviations, or suppress specific folders completely by mapping them to an empty alias (`""`).
- **Comment-Preserving Vault Configuration**:
  - Vault-level configuration is written directly to `<vault>/m2a.toml` using `tomlkit`, preserving all existing comments and formatting.

---

## Dashboard Overview

Open the dashboard inside Anki via **Tools $\rightarrow$ VaultSync Dashboard**:

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
│ Actions:                                                        │
│  [     Sync (15)     ]  [ Preview Diff (3) ]  [ Rescan Vault ]  │
└─────────────────────────────────────────────────────────────────┘
```

* **Sync**: Writes markdown card IDs to disk, imports new notes, and commits field/tag updates to Anki.
* **Preview Diff**: Opens the diff inspector to review and selectively approve updates before committing them.
* **Rescan Vault**: Re-reads all Markdown files in the vault and compares them against the current collection.

---

## Comprehensive Settings Guide

The Settings window (`ConfigWindow`) is organized into six specialized tabs:

### 1. General
* **Obsidian Vault Folder**: Path to your local Markdown vault directory.
* **Image Occlusion Base Folder**: Directory where occlusion images are exported into an `occlusions/` subfolder alongside an `index.md` tag map.
* **Sync Behavior**:
  * *Show Diff Preview Dialog Before Updating Modified Cards*: Always prompts with the visual diff window prior to modifying existing notes.
  * *Automatically Close Dashboard on Successful Sync*: Auto-dismisses the dashboard when synchronization finishes with zero errors.
  * *Auto-sync on Anki Startup*: Runs a background scan and synchronization when Anki launches.
* **Tag Formatting**:
  * *Base Tag Prefix*: Optional root tag prepended to all synced cards (e.g. `vault`).
  * *Pad Single-Digit Tag Numbers*: Formats single-digit folder names with leading zeros (e.g. `'1. Chapter'` $\rightarrow$ `'01_Chapter'`) for natural sorting in Anki's card browser. Alphanumeric terms like `MP3` or `ISO9001` are automatically protected.

### 2. Deck Routing
* **Default Target Deck**: Dropdown selecting the fallback Anki deck for notes that do not match any routing rules.
* **Deck Routing Rules**:
  * An interactive table mapping vault folder paths to destination Anki decks.
  * Uses **Longest-Prefix Matching**: a rule for `computer_science/algorithms` takes precedence over a rule for `computer_science`.
  * Includes a **Browse Folder...** button to easily select relative vault directories.

### 3. Tag Aliasing
* **Tag Path Aliases**:
  * Map folder paths to custom hierarchical tags (e.g. `"pen and paper"` $\rightarrow$ `"work::writing"`).
  * **Tag Suppression**: Set the replacement to an empty value to omit that folder level from generated tags (useful for non-topical organization folders like `archive` or `general`).
* Aliased tags are authoritative and exempt from automatic digit padding.

### 4. Card Rules and Media
* **Parsing & Media Options**:
  * *Enable Cloze Syntax Processing*: Supports `==cloze==`, `{cloze}`, and native Anki `{{c1::...}}` syntax.
  * *Hash Media*: Renames embedded images and attachments to deterministic SHA-256 hashes (`<8hex>_01.ext`) to avoid naming collisions.
  * *Re-stage Already Flagged Image Occlusions*: Forces re-exporting image occlusions during routine syncs.
* **Ignored Directories & Files**:
  * Multi-line text field for glob patterns or folders to skip during scanning (e.g. `.obsidian`, `templates/*`, `archive`, `ai`).

### 5. Advanced
* **Custom Note Type Bindings**:
  * Bind VaultSync cards to existing Anki Note Types instead of the default `M2A Basic` and `M2A Cloze` templates.
* **Maintenance & Diagnostic Tools**:
  * *Repair / Re-apply M2A Note Type Templates*: Restores standard CSS styles and card front/back layouts.
  * *Find & View Orphaned Cards in Browser*: Opens Anki's native browser searching for notes whose Obsidian source files no longer exist.

### 6. Logs
* Live reverse-chronological log viewer showing recent operations and warnings across sessions.
* **Open Logs Folder** and **Clear Logs** buttons for diagnostic maintenance.

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
````

---

## Installation & Development Setup

### Symlink Installation
Symlink this repository into your Anki `addons21` directory:

```bash
# Linux
ln -s "/home/leon/Coding/Anki Vault Synchronization/Anki-VaultSync" ~/.local/share/Anki2/addons21/anki-vaultsync

# macOS
ln -s "/path/to/Anki-VaultSync" ~/Library/Application\ Support/Anki2/addons21/anki-vaultsync

# Windows (Command Prompt as Administrator)
mklink /D "%APPDATA%\Anki2\addons21\anki-vaultsync" "C:\path\to\Anki-VaultSync"
```

### Vendoring the Engine
To update the bundled `markdown2anki` core engine from the local sibling repository:

```bash
python3 scripts/bundle.py
```

### Running Tests
Execute the automated test suite using pytest with headless Qt:

```bash
QT_QPA_PLATFORM=offscreen pytest tests/
```

> [!NOTE]
> The test suite includes an automated `conftest.py` fixture that safely mocks all modal `QMessageBox` popups, guaranteeing non-blocking test execution in headless CI environments.
