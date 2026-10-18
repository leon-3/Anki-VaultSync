"""Centralized help tooltips for Anki-VaultSync configuration and dashboard."""
from __future__ import annotations

TOOLTIPS: dict[str, str] = {
    "suspend_new_clozes": (
        "When enabled, newly created Text-Cloze cards are added in a suspended state. "
        "This allows you to pace your study and selectively unsuspend cards in Anki when ready."
    ),
    "image_occlusion_mode": (
        "Controls how Image Occlusion cards (Image Cloze) are processed. "
        "'Direct Native Image Occlusions' creates notes using Anki's built-in Image Occlusion notetype "
        "with image assets placed directly into collection.media. 'Folder Staging' exports images to "
        "an external occlusions/ folder for manual import."
    ),
    "suspend_new_occlusions": (
        "Automatically suspends newly created Image Occlusion cards until you draw masks in Anki's editor. "
        "This prevents 'Empty Card' review errors while preserving full deck routing and tagging."
    ),
    "freeze_image_occlusions": (
        "When enabled, once an Image Occlusion card has been synced and flagged, routine syncs will skip it entirely "
        "and never check for updates."
    ),
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
    "missing_in_anki": (
        "Cards that possess sync IDs in Obsidian notes but do not exist in Anki (for instance, because they were deleted in Anki). "
        "VaultSync never recreates these cards automatically. You can either mark them as DELETED in Obsidian to permanently deactivate them, "
        "or selectively re-sync them back into Anki."
    ),
    "orphaned_cards": (
        "Finds cards in Anki originally created by VaultSync/Markdown2Anki whose source note was deleted from your Obsidian vault, and opens them in Anki's Card Browser."
    ),
    "shortcuts_open_dashboard": (
        "Keyboard shortcut to open the VaultSync Dashboard."
    ),
    "shortcuts_quick_sync": (
        "Keyboard shortcut to quickly sync new cards from your Obsidian vault without updating modified cards."
    ),
    "shortcuts_full_sync": (
        "Keyboard shortcut to sync new cards and update modified cards from your Obsidian vault."
    ),
}
