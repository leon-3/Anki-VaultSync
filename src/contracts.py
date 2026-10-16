from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ModelName = Literal["M2A Basic", "M2A Cloze"]


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
    card: Any | None = None


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
    card: Any | None = None
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
    preview_question: str


@dataclass
class SyncPlan:
    vault_path: Path
    default_deck: str
    file_plans: list[FileSyncPlan]
    occlusions_to_stage: list[Any] = field(default_factory=list)
    orphans: list[OrphanNoteRecord] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)

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


@dataclass
class SyncReport:
    created_count: int = 0
    updated_count: int = 0
    deck_moved_count: int = 0
    media_added_count: int = 0
    occlusions_staged_count: int = 0
    staged_occlusions_by_tag: dict[str, list[str]] = field(default_factory=dict)
    occlusion_dir: Path | None = None
    skipped_files: list[tuple[Path, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    externally_modified_files: list[Path] = field(default_factory=list)
