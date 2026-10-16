"""Centralized help tooltips for Anki-VaultSync configuration and dashboard."""
from __future__ import annotations

TOOLTIPS: dict[str, str] = {
    "vault_path": (
        "The root directory of your Obsidian or Markdown vault where your flashcard notes are located. "
        "VaultSync scans this directory and its subfolders for cards."
    ),
    "occlusion_dir": (
        "Destination directory where image occlusion images are staged with generated hashes. "
        "You can import or reference these images directly when creating image occlusion cards in Anki."
    ),
    "default_deck": (
        "Fallback Anki deck where cards will be placed if no course, folder routing rule, "
        "or note-specific deck declaration matches."
    ),
    "base_tag": (
        "An optional prefix added to all generated hierarchical tags. "
        "For example, setting 'University' creates tags like 'University::CompSci::Algorithms'."
    ),
    "hash_media": (
        "Renames embedded images using a deterministic 8-character lowercase hex hash (e.g. 472d6b17_01.png). "
        "This prevents filename collisions and case-sensitivity issues across multiple devices."
    ),
    "cloze_notes": (
        "When enabled, questions titled 'Cloze' with {{c1::...}} markers are converted into native Anki Cloze "
        "cards instead of basic Q&A cards."
    ),
    "re_stage_occlusions": (
        "When unchecked, already-flagged image occlusions are skipped during updates to prevent duplicate image files "
        "in your occlusions folder. Check this only to force re-exporting image occlusion sources."
    ),
    "show_diff_before_update": (
        "When enabled, opens a visual diff dialog before committing updates, showing exactly which fields, tags, "
        "or decks changed, allowing you to selectively approve updates."
    ),
    "auto_close_on_success": (
        "Automatically closes the VaultSync Dashboard upon successful sync when there are no errors or warnings."
    ),
    "repair_templates": (
        "Re-applies the latest HTML and CSS card templates from Markdown2Anki to the configured note types "
        "in your Anki collection without touching your card data or scheduling."
    ),
    "basic_model_id": (
        "Specify an existing Anki note type by ID or name to use for Basic cards. "
        "VaultSync will use this note type's existing styling, templates, and field layout "
        "instead of generating a new note type."
    ),
    "cloze_model_id": (
        "Specify an existing Anki note type by ID or name to use for Cloze cards. "
        "VaultSync will use this note type's existing styling, templates, and field layout "
        "instead of generating a new note type."
    ),
    "decks": (
        "Folder routing rules that map relative vault folders or courses to specific target Anki decks. "
        "For example, 'everyday' -> '01. everyday (focus)'."
    ),
    "subdirectories": (
        "Legacy subdirectory mapping for academic mode (maps subfolders like 'Anki - Lectures' to tag 'Lectures'). "
        "An empty table enables full recursive walking at arbitrary depth."
    ),
    "ignore": (
        "List of folders or glob patterns to ignore during vault traversal. "
        "Files inside these directories will not be parsed."
    ),
}
