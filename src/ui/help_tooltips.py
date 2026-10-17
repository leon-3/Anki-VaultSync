"""Centralized help tooltips for Anki-VaultSync configuration and dashboard."""
from __future__ import annotations

TOOLTIPS: dict[str, str] = {
    "vault_path": (
        "The root directory of your Obsidian or Markdown vault where your flashcard notes are located. "
        "VaultSync scans this directory and its subfolders for cards."
    ),
    "occlusion_dir": (
        "Base directory where image occlusions are stored. "
        "VaultSync will automatically create and save exported images into an 'occlusions/' subfolder inside this directory "
        "(e.g. /your_folder/occlusions/). An index.md mapping each tag to its generated image files will also be created inside that folder."
    ),
    "default_deck": (
        "Fallback Anki deck where cards will be placed if no folder routing rule "
        "or note-specific deck declaration matches."
    ),
    "base_tag": (
        "An optional prefix added to all generated hierarchical tags. "
        "For example, setting 'Knowledge' creates tags like 'Knowledge::TopicA::SubtopicB'."
    ),
    "pad_single_digit_tags": (
        "Automatically zero-pads single-digit prefix numbers in tag segments (e.g. '1. Topic' -> '01_Topic'). "
        "Numbers inside words or decimals (e.g. '2D', 'v1.2') are preserved."
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
    "sync_on_startup": (
        "When enabled, VaultSync automatically runs a background synchronization of your Obsidian vault "
        "each time Anki opens this profile. If no changes were detected, it stays quiet; "
        "if cards were added or modified, a brief notification tooltip appears."
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
    "deck_routing": (
        "Folder routing rules that map relative vault folder paths to specific target Anki decks using "
        "longest prefix matching (e.g. 'TopicA' -> 'Domain::TopicA', 'TopicA/SubtopicB' -> 'Domain::SubtopicB')."
    ),
    "decks": (
        "Folder routing rules that map relative vault folder paths to specific target Anki decks."
    ),
    "tag_aliases": (
        "Path-based tag aliases that rename or suppress specific vault folders when generating hierarchical Anki tags. "
        "For example, 'TopicA/SubtopicB' -> 'CustomTag', or 'Notes' -> '' to suppress."
    ),
    "subdirectories": (
        "Legacy subdirectory mapping table. Replaced by modern recursive traversal and deck routing."
    ),
    "ignore": (
        "List of folders or glob patterns to ignore during vault traversal. "
        "Files inside these directories will not be parsed."
    ),
    "orphaned_cards": (
        "Finds cards in Anki originally created by VaultSync/Markdown2Anki whose source note was deleted from your Obsidian vault, and opens them in Anki's Card Browser."
    ),
}
