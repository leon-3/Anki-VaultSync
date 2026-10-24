"""Centralized help tooltips for Anki-VaultSync configuration and dashboard."""
from __future__ import annotations

TOOLTIPS: dict[str, str] = {
    "backup_before_sync": (
        "Automatically creates an Anki collection backup before modifying or deleting cards in Anki. "
        "Backups are safely managed by Anki's native backup service and can be restored anytime "
        "via Anki's profile manager (File -> Switch Profile -> Open Backup)."
    ),
    "suspend_new_clozes": (
        "When enabled, newly created Cloze cards that do not yet have cloze {{ deletions are added in a suspended state. "
        "Cards that already contain cloze deletions will remain active. Unmasked cards are unsuspended once clozes are added in Anki."
    ),
    "image_occlusion_mode": (
        "Controls how Image Occlusion cards (Image Cloze) are processed. "
        "'Direct Native Image Occlusions' creates notes using Anki's built-in Image Occlusion notetype "
        "with image assets placed directly into collection.media. 'Folder Staging' exports images to "
        "an external occlusions/ folder for manual import."
    ),
    "auto_unflag_occlusions": (
        "When enabled, automatically unsuspend image occlusion cards and remove their TODO_PROCESS_OCCLUSION tag during sync once masks have been drawn in Anki."
    ),
    "suspend_new_occlusions": (
        "Automatically suspends newly created Image Occlusion cards until you draw masks in Anki's editor. "
        "This prevents 'Empty Card' review errors while preserving full deck routing and tagging."
    ),
    "freeze_image_occlusions": (
        "When enabled, existing Image Occlusion notes in Anki will never be overwritten or updated during routine synchronization. "
        "This protects your hand-drawn occlusion masks, shapes, and custom card edits in Anki from being modified by changes in Obsidian."
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
        "Anki stores all media files in a single shared folder. Enabling this gives each image a unique name based on its content, "
        "preventing different images with the same filename (e.g. 'image.png') from accidentally overwriting each other."
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
    "show_status_bar": (
        "When enabled, displays a minimalist status indicator in the bottom-right corner of Anki's main window. "
        "It subtly indicates whether changes are pending in your Obsidian vault, shows sync progress, "
        "and provides quick access to sync actions."
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
        "Folder deck rules that map relative vault folder paths to specific target Anki decks using "
        "longest prefix matching (e.g. 'TopicA' -> 'Domain::TopicA', 'TopicA/SubtopicB' -> 'Domain::SubtopicB')."
    ),
    "decks": (
        "Folder deck rules that map relative vault folder paths to specific target Anki decks."
    ),
    "tag_aliases": (
        "Folder tag rules that rename, alias, or suppress specific vault folders when generating hierarchical Anki tags. "
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

TOOLTIP_TITLES: dict[str, str] = {
    "backup_before_sync": "Automatic Backup",
    "suspend_new_clozes": "Suspend Unmasked Clozes",
    "image_occlusion_mode": "Image Occlusion Mode",
    "auto_unflag_occlusions": "Auto-unflag Completed Occlusions",
    "suspend_new_occlusions": "Suspend New Occlusions",
    "freeze_image_occlusions": "Skip Image Occlusion Updates",
    "vault_path": "Vault Directory",
    "occlusion_dir": "Occlusion Directory",
    "default_deck": "Default Target Deck",
    "base_tag": "Base Tag Prefix",
    "pad_single_digit_tags": "Pad Single-Digit Tags",
    "hash_media": "Unique Image Names",
    "cloze_notes": "Convert Cloze Questions",
    "re_stage_occlusions": "Re-export Occlusions",
    "show_diff_before_update": "Interactive Diff Preview",
    "sync_on_startup": "Auto-sync on Startup",
    "show_status_bar": "Status Bar Indicator",
    "auto_close_on_success": "Close Dashboard on Success",
    "basic_model_id": "Basic Note Type",
    "cloze_model_id": "Cloze Note Type",
    "deck_routing": "Folder Deck Rules",
    "decks": "Folder Deck Rules",
    "tag_aliases": "Folder Tag Rules",
    "subdirectories": "Subdirectory Mapping",
    "ignore": "Ignored Directories & Files",
    "missing_in_anki": "Cards Deleted in Anki",
    "orphaned_cards": "Orphaned Notes in Anki",
    "shortcuts_open_dashboard": "Open Dashboard Shortcut",
    "shortcuts_quick_sync": "Quick Sync Shortcut",
    "shortcuts_full_sync": "Full Sync Shortcut",
}


def format_tooltip_box(key: str, fallback_title: str | None = None) -> str:
    """Format a clean, card-like hover box with a title and structured multi-line explanation."""
    text = TOOLTIPS.get(key, "")
    if not text:
        return ""
    title = TOOLTIP_TITLES.get(key, fallback_title or key.replace("_", " ").title())
    return (
        f"<div style='min-width: 220px; max-width: 320px; padding: 2px;'>"
        f"<div style='font-size: 12px; font-weight: bold; color: #2196F3; margin-bottom: 4px;'>{title}</div>"
        f"<div style='font-size: 11px; color: #e6e6e6; line-height: 140%;'>{text}</div>"
        f"</div>"
    )
