from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from markdown2anki.build import ImageOcclusion
    from markdown2anki.parser import Card

ModelName = str


@dataclass(frozen=True)
class ExistingNoteRecord:
    note_id: int
    card_id: str
    model_id: int
    model_name: ModelName
    fields: dict[str, str]
    tags: list[str]
    deck_ids: set[int]


@dataclass(frozen=True)
class MediaItem:
    source_path: Path
    staged_filename: str  # Expected filename produced by Stage 1 renderer


@dataclass(frozen=True)
class NoteCreateOp:
    card_id: str
    model_name: ModelName
    target_deck_name: str
    fields: dict[str, str]  # Includes <!--m2a:card_id--> on Back / Back Extra
    tags: list[str]
    source_file: Path
    source_line: int
    media_refs: list[MediaItem]
    card: Card | None = None


@dataclass
class NoteUpdateOp:
    note_id: int
    card_id: str
    model_name: ModelName
    target_deck_name: str
    old_deck_names: list[str]
    old_fields: dict[str, str]
    new_fields: dict[str, str]
    old_tags: list[str]
    new_tags: list[str]
    deck_changed: bool
    fields_changed: bool
    tags_changed: bool
    source_file: Path
    source_line: int
    media_refs: list[MediaItem]
    card: Card | None = None
    approved: bool = True


@dataclass(frozen=True)
class FileSyncPlan:
    file_path: Path
    mtime_ns: int
    sha256_hex: str
    creates: list[NoteCreateOp]
    updates: list[NoteUpdateOp]
    unchanged_count: int


@dataclass(frozen=True)
class OrphanNoteRecord:
    note_id: int
    card_id: str
    model_name: ModelName
    preview_question: str = ""
    deck_names: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MissingInAnkiRecord:
    card_id: str
    file_path: Path
    line: int
    preview_question: str
    target_deck: str
    tags: list[str] = field(default_factory=list)
    card: Card | None = None


@dataclass
class SyncPlan:
    vault_path: Path
    default_deck: str
    file_plans: list[FileSyncPlan]
    occlusions_to_stage: list[ImageOcclusion] = field(default_factory=list)
    orphans: list[OrphanNoteRecord] = field(default_factory=list)
    missing_in_anki: list[MissingInAnkiRecord] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    suspend_new_occlusions: bool = True
    suspend_new_clozes: bool = False
    hash_media: bool = True

    @property
    def total_files(self) -> int:
        return len(self.file_plans)

    @property
    def total_creates(self) -> int:
        return sum(len(fp.creates) for fp in self.file_plans)

    @property
    def total_updates(self) -> int:
        return sum(len(fp.updates) for fp in self.file_plans)

    @property
    def approved_updates(self) -> int:
        return sum(1 for fp in self.file_plans for u in fp.updates if u.approved)

    @property
    def total_unchanged(self) -> int:
        return sum(fp.unchanged_count for fp in self.file_plans)

    @property
    def total_missing_in_anki(self) -> int:
        return len(self.missing_in_anki)


@dataclass
class SyncReport:
    created_count: int = 0
    created_image_cloze_count: int = 0
    created_text_cloze_count: int = 0
    created_basic_count: int = 0
    updated_count: int = 0
    updated_image_cloze_count: int = 0
    updated_text_cloze_count: int = 0
    updated_basic_count: int = 0
    deck_moved_count: int = 0
    media_added_count: int = 0
    occlusions_staged_count: int = 0
    suspended_count: int = 0
    staged_occlusions_by_tag: dict[str, list[str]] = field(default_factory=dict)
    occlusion_dir: Path | None = None
    skipped_files: list[tuple[Path, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    externally_modified_files: list[Path] = field(default_factory=list)
    flagged_cards: list[Card] = field(default_factory=list)
