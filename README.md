# Anki-VaultSync

[![Release](https://img.shields.io/badge/version-1.0.0-blue?style=flat-square)](https://github.com/leon-3/Anki-VaultSync/releases)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](https://github.com/leon-3/Anki-VaultSync/blob/main/LICENSE)
[![Anki](https://img.shields.io/badge/Anki-23.10%2B-informational?style=flat-square)](https://apps.ankiweb.net)
[![AnkiWeb](https://img.shields.io/badge/AnkiWeb-1811062412-orange?style=flat-square)](https://ankiweb.net/shared/info/1811062412)

**AnkiWeb Add-on Code:** `1811062412` | **Listing:** [ankiweb.net/shared/info/1811062412](https://ankiweb.net/shared/info/1811062412)

Anki-VaultSync is an Anki Desktop add-on that syncs flashcards from Obsidian or Markdown files directly into your local Anki collection. It connects directly to Anki's database (`mw.col`) in-process, without needing background services, AnkiConnect, or terminal commands.

It uses the card format from [Markdown2Anki](https://github.com/leon-3/Markdown2Anki) and pairs with two companion Obsidian plugins:
- [Anki Card Styling](https://github.com/leon-3/Obsidian-Anki-Card-Highlighting): Shows cloze badges, card types, Extra Question Line (`EQL:`) tags, and status bars in Obsidian.
- [Anki Cloze Deletion](https://github.com/leon-3/Obsidian-Anki-Cloze): Gives you quick shortcuts (`Ctrl+Shift+C` and `Ctrl+Alt+C`) to create clozes scoped to each card block.
- [Markdown2Anki](https://github.com/leon-3/Markdown2Anki): The underlying parser engine and standalone CLI tool.

---

## Highlights & Key Features

- Direct sync without background servers: Connects straight to Anki's database (`mw.col`), making syncs fast and supporting Anki's native Undo (`Ctrl+Z`).
- Safe card updates: Writes unique IDs (`%%id%%`) to your Markdown notes on disk before saving to Anki, preventing duplicates or lost edits if interrupted.
- Preserves your review history: Updates existing cards in place without resetting intervals, ease factors, or learning steps (even if cards are in filtered/cram decks).
- Visual dashboard & diff viewer: Review pending card additions, modifications, and deletions with an interactive HTML diff before applying any changes.
- Status bar indicator: Shows pending cards right in Anki's bottom status bar (`VaultSync: X new, Y mod`) with a right-click menu for quick syncs.
- Automatic media sync: Copies linked images, audio, and occlusion masks to your Anki `collection.media` folder automatically.

---

## Installation & Quickstart

### Method 1: AnkiWeb (Recommended)
1. In Anki Desktop, go to **Tools** > **Add-ons**.
2. Click **Get Add-ons...**.
3. Enter the add-on code: `1811062412` and click **OK**.
4. Restart Anki.

### Method 2: Manual Installation from Release Archive
1. Download `anki-vaultsync.ankiaddon` from [GitHub Releases](https://github.com/leon-3/Anki-VaultSync/releases).
2. In Anki Desktop, go to **Tools** > **Add-ons**.
3. Click **Install from file...** and select the downloaded `.ankiaddon` file.
4. Restart Anki.

### Quickstart Setup
1. Open the dashboard via **Tools** > **VaultSync Dashboard** (or press `Ctrl+Alt+V`).
2. Click **Settings** in the dashboard toolbar.
3. Select your Obsidian Vault directory.
4. Set your default deck name (e.g. `Default` or `Obsidian::Inbox`).
5. Click **Save Configuration**, return to the dashboard, and click **Sync**.

---

## Documentation

### 1. Card Syntax & Identifiers

#### Card Delimiters (`---`)
Cards are enclosed between standard horizontal rules (`---`). Any content outside these delimiters (regular notes, lecture summaries, outlines) is completely ignored during synchronization.

#### Basic Q&A Cards
The question is placed on the line directly below `---`. All following lines before the next `---` become the answer:

```markdown
---
Where does cellular respiration occur?
It takes place primarily in the mitochondria of eukaryotic cells.

---
```

#### Sync Status & Card IDs (`ADDED:` / `%%id%%`)
When a card is synced for the first time, Anki-VaultSync marks the question line with `ADDED:` and appends a unique 6-character ID (`%%id%%`):

```markdown
---
ADDED: Where does cellular respiration occur? %%k8f2a1%%
It takes place primarily in the mitochondria of eukaryotic cells.

---
```

- **Editing Synced Cards:** You can freely update the question or answer in Markdown. As long as `%%id%%` remains intact, Anki-VaultSync updates the existing Anki note in place without resetting review statistics or scheduling.
- **Safety Guarantee:** The add-on writes `%%id%%` to your disk file before inserting the card into Anki's database, preventing duplicates if interrupted.

#### Deleting Cards (`DELETED:`)
To remove a card from Anki, replace `ADDED:` with `DELETED:`:

```markdown
---
DELETED: Where does cellular respiration occur? %%k8f2a1%%
It takes place primarily in the mitochondria of eukaryotic cells.

---
```
On the next sync, the card is deleted from Anki, and Anki-VaultSync cleans up the note block.

#### Extra Question Lines (`EQL:`)
If a basic Q&A card needs an extra subtitle, context, or prompt on the front of the card, add `EQL:` on the line directly below the question:

```markdown
---
Where does cellular respiration occur?
EQL: Mention both the organelle and the energy molecule produced.
It takes place in the mitochondria and produces ATP.

---
```
The line with `EQL:` is rendered as a distinct subtitle beneath the main question on the front of the Anki card.

---

### 2. Cloze Deletion & Image Occlusion

#### Text Cloze Cards (`Cloze`)
To create a Cloze card, write `Cloze` on the line directly after `---`, followed by your text containing standard cloze markers `{{c1::answer}}` or `{{c1::answer::hint}}`:

```markdown
---
Cloze
Glycolysis occurs in the {{c1::cytoplasm}} and produces a net yield of {{c2::2 ATP}} molecules.

---
```

Once synced, `ADDED:` and the card ID are placed directly on the `Cloze` header line:

```markdown
---
ADDED: Cloze %%d4f8k2%%
Glycolysis occurs in the {{c1::cytoplasm}} and produces a net yield of {{c2::2 ATP}} molecules.

---
```

- **Unmasked Clozes & Tagging:** If you create a `Cloze` card that does not yet contain any `{{c1::...}}` deletions, the card is exported as a Cloze note and tagged with `TODO_PROCESS_CLOZES`.
- **Automatic Suspend & Unsuspend:** Unmasked cloze cards are automatically suspended in Anki so you do not see empty cards during study. Once you add the cloze deletions, the next sync detects the completed card, automatically unsuspends it, and removes the `TODO_PROCESS_CLOZES` tag.

#### Image Occlusion Cards (`Image Cloze`)
To create an image occlusion card, write `Image Cloze` on the line directly after `---`, followed by a linked image:

```markdown
---
Image Cloze
![[Synapse-Diagram.png]]

---
```

Synced state:

```markdown
---
ADDED: Image Cloze %%o4fj5k3%%
![[Synapse-Diagram.png]]

---
```

Images linked inside cards are automatically hashed and copied to your Anki `collection.media` folder.

- **Native vs. Legacy Mode:**
  - **Native Mode (Default):** Creates native Anki Image Occlusion notes directly in your collection. Newly synced cards are tagged with `TODO_PROCESS_OCCLUSION` and kept suspended until you open Anki and draw your occlusion masks using Anki's built-in Image Occlusion editor.
  - **Legacy Staging Mode (`stage`):** Exports the raw image to an external staging folder for manual occlusion workflows without native Anki occlusion notes.
- **Automatic Unsuspend on Completion:** Once you draw your masks in Anki's native editor, Anki-VaultSync automatically recognizes that the card has masks, unsuspends the card for your review queue, and removes the `TODO_PROCESS_OCCLUSION` tag.

---

### 3. Tag Generation & Heading Hierarchy

Tags in Anki are generated automatically from the note's folder location and the Markdown headings (`#`, `##`, `###`) placed above the card:

```markdown
# Biology
## Biochemistry

---
What is the function of ATP synthase?
Synthesizes ATP from ADP and inorganic phosphate.

---
```

This card is assigned the hierarchical Anki tag: `Biology::Biochemistry`.

#### Numbered Tag Padding (`pad_single_digit_tags`)
When enabled in settings, single-digit numbers followed by a space or punctuation in folder names and headings (e.g. `1. Anatomy` or `2-Biochemistry`) are automatically padded with a leading zero (`01_Anatomy::02_Biochemistry`). This ensures tags sort in natural numerical order in Anki's sidebar.

---

### 4. Tag & Deck Routing

You can customize where cards are placed and how tags are named based on their folder in your vault:

#### Deck Routing (Where cards go)
Deck routing maps vault folders to specific Anki decks using Longest Prefix Matching (LPM):

| Vault Folder Path | Target Deck in Anki |
| :--- | :--- |
| `Medicine` | `University::Medicine` |
| `Medicine/Cardiology` | `University::Medicine::Cardiology` |

- A card in `Medicine/Cardiology/Notes.md` goes to `University::Medicine::Cardiology`.
- A card in `Medicine/Anatomy/Notes.md` falls back to `University::Medicine`.
- Cards outside configured folders go to your Default Deck.

#### Tag Aliases (What tags get replaced)
Tag rules let you shorten, rename, or hide specific folder segments in the generated Anki tags:

| Vault Folder Path | Tag Alias | Result in Anki Tag |
| :--- | :--- | :--- |
| `Courses/Semester_1` | `Sem1` | Folder segment `Courses/Semester_1` becomes `Sem1` |
| `Drafts` | `""` | Segment `Drafts` is completely removed from the tag |

---

### 5. Settings Reference

Access add-on configuration via **Tools** > **VaultSync Dashboard** > **Settings**:

#### General Settings
- **Vault Directory**: Path to your local Obsidian vault.
- **Default Deck**: Fallback deck for cards that do not match any folder routing rule (subdecks separated by `::`).
- **Sync on Startup**: Runs a sync check automatically every time Anki is launched.
- **Show Diff Before Update**: Opens an interactive HTML diff preview before modifying or deleting cards in Anki.
- **Auto Close on Success**: Automatically closes the sync dialog once finished if there are no errors.
- **Backup Before Sync**: Creates an undo snapshot before applying database modifications.
- **Show Status Bar Indicator**: Toggles the live counter in Anki's bottom status bar.
- **Auto Unflag Occlusions**: Automatically unsuspends completed Image Occlusions and Clozes and removes their `TODO_PROCESS` tags once masks or deletions are added.

#### Keyboard Shortcuts
- **Open Dashboard**: Default `Ctrl+Alt+V`
- **Quick Sync**: Default `Ctrl+Alt+S`
- **Full Sync**: Default `Ctrl+Alt+U`

---

## Development & Building

### Requirements
- Python 3.10+
- PySide6 / PyQt6
- Anki 23.10+

### Running Tests
Run the test suite (138 tests):

```bash
pytest Anki-VaultSync/tests
```

### Building the Package
Packages the add-on into `dist/anki-vaultsync.ankiaddon`:

```bash
python Anki-VaultSync/scripts/bundle.py --package
```

### Flatpak Permissions (Linux)
If running Anki through Flatpak, grant filesystem permissions to your home directory:

```bash
flatpak override --user --filesystem=home net.ankiweb.Anki
```
