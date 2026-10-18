"""Welcome note generator for newly onboarded Obsidian vaults."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("anki_vaultsync")

WELCOME_NOTE_NAME = "VaultSync Welcome.md"

WELCOME_NOTE_CONTENT = """# VaultSync Welcome
---
What is VaultSync?
A tool to seamlessly synchronize Markdown flashcards from Obsidian into Anki.

---
Cloze
VaultSync supports {{c1::cloze deletions}} and {{c2::image occlusions}} out of the box.
"""


def create_welcome_note(vault_path: Path) -> Path:
    """Create a sample welcome note in the given vault directory if it doesn't already exist.

    The note follows strict markdown2anki card syntax (one basic card and one cloze note).
    Does not overwrite existing files. Returns the Path to the welcome note file.
    """
    note_path = vault_path / WELCOME_NOTE_NAME
    if note_path.exists():
        logger.info(f"Welcome note already exists at {note_path}, skipping creation.")
        return note_path

    try:
        note_path.write_text(WELCOME_NOTE_CONTENT, encoding="utf-8")
        logger.info(f"Created sample welcome note at {note_path}")
    except OSError as exc:
        logger.warning(f"Failed to create welcome note at {note_path}: {exc}")
        raise

    return note_path
