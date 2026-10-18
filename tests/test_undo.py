from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from anki.collection import Collection
from markdown2anki.flags import write_flags
from markdown2anki.parser import parse_file

from src.contracts import SyncPlan
from src.undo import (
    attach_redo_hook,
    attach_undo_hook,
    clear_sync_undo_stack,
    get_redo_stack_depth,
    get_undo_stack_depth,
    on_state_did_redo,
    on_state_did_undo,
    pop_last_sync_cards,
    pop_last_sync_redo_cards,
    register_sync_added_cards,
)
from src.worker import build_sync_plan, execute_sync_plan


def test_undo_stack_basic_push_pop():
    clear_sync_undo_stack()
    assert get_undo_stack_depth() == 0

    c1 = SimpleNamespace(location="a.md:1")
    c2 = SimpleNamespace(location="b.md:2")
    register_sync_added_cards([c1])
    register_sync_added_cards([c2])
    assert get_undo_stack_depth() == 2

    assert pop_last_sync_cards() == [c2]
    assert pop_last_sync_cards() == [c1]
    assert pop_last_sync_cards() is None
    assert get_undo_stack_depth() == 0


def test_undo_stack_clear():
    clear_sync_undo_stack()
    register_sync_added_cards([SimpleNamespace(location="a.md:1")])
    assert get_undo_stack_depth() == 1
    clear_sync_undo_stack()
    assert get_undo_stack_depth() == 0


def test_on_state_did_undo_ignores_other_operations():
    clear_sync_undo_stack()
    c = SimpleNamespace(location="a.md:1")
    register_sync_added_cards([c])

    # Another operation, e.g. "Add Note"
    on_state_did_undo(SimpleNamespace(operation="Add Note"))
    # Stack must NOT be popped
    assert get_undo_stack_depth() == 1
    clear_sync_undo_stack()


def test_on_state_did_undo_clears_flags_in_markdown():
    clear_sync_undo_stack()
    with tempfile.TemporaryDirectory() as td:
        md_file = Path(td) / "test.md"
        md_file.write_text("---\nWhat is a gene?\nA unit of heredity.\n", encoding="utf-8")

        cards = parse_file(md_file)
        assert len(cards) == 1

        # Simulate sync flagging the card
        write_flags([(cards[0], "gene01")])
        assert "ADDED: What is a gene? %%gene01%%" in md_file.read_text(encoding="utf-8")

        # Register cards on undo stack
        register_sync_added_cards([cards[0]])

        # Simulate Anki undo
        on_state_did_undo(SimpleNamespace(operation="VaultSync"))

        # Verify flag is cleared
        content = md_file.read_text(encoding="utf-8")
        assert "ADDED" not in content
        assert "%%gene01%%" not in content
        assert "What is a gene?" in content


def test_on_state_did_undo_lifo_order():
    clear_sync_undo_stack()
    with tempfile.TemporaryDirectory() as td:
        f1 = Path(td) / "f1.md"
        f1.write_text("---\nCard 1?\nAns 1.\n", encoding="utf-8")
        f2 = Path(td) / "f2.md"
        f2.write_text("---\nCard 2?\nAns 2.\n", encoding="utf-8")

        cards1 = parse_file(f1)
        cards2 = parse_file(f2)

        # Sync 1 flags Card 1
        write_flags([(cards1[0], "c10001")])
        register_sync_added_cards([cards1[0]])

        # Sync 2 flags Card 2
        write_flags([(cards2[0], "c20002")])
        register_sync_added_cards([cards2[0]])

        assert "ADDED" in f1.read_text(encoding="utf-8")
        assert "ADDED" in f2.read_text(encoding="utf-8")

        # Undo 1: pops Sync 2 (Card 2)
        on_state_did_undo(SimpleNamespace(operation="VaultSync"))
        assert "ADDED" in f1.read_text(encoding="utf-8")  # Card 1 still flagged
        assert "ADDED" not in f2.read_text(encoding="utf-8")  # Card 2 unflagged

        # Undo 2: pops Sync 1 (Card 1)
        on_state_did_undo(SimpleNamespace(operation="VaultSync"))
        assert "ADDED" not in f1.read_text(encoding="utf-8")  # Card 1 now also unflagged


def test_end_to_end_sync_and_undo_unflags_card_in_vault():
    clear_sync_undo_stack()
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text('vault = "."\ndeck = "TestDeck"\n', encoding="utf-8")
        note_file = vault_path / "card.md"
        note_file.write_text("# Test\n---\nWhat is entropy?\nMeasure of disorder.\n", encoding="utf-8")

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            # 1. First sync adds the card
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 1

            report = execute_sync_plan(col, plan)
            assert report.created_count == 1
            assert len(report.flagged_cards) == 1
            assert "ADDED:" in note_file.read_text(encoding="utf-8")

            # 2. Trigger native Anki undo
            undo_res = col.undo()
            assert undo_res.operation == "VaultSync"

            # 3. Anki's state_did_undo hook is called
            on_state_did_undo(undo_res)

            # 4. Note in vault is unflagged!
            content_after_undo = note_file.read_text(encoding="utf-8")
            assert "ADDED:" not in content_after_undo
            assert "What is entropy?" in content_after_undo

            # 5. Subsequent sync treats it as a new create again instead of an orphan
            plan_after_undo = build_sync_plan(col, vault_path)
            assert plan_after_undo.total_creates == 1
            assert len(plan_after_undo.missing_in_anki) == 0
        finally:
            col.close()


def test_on_state_did_redo_restores_flags_in_markdown():
    clear_sync_undo_stack()
    with tempfile.TemporaryDirectory() as td:
        md_file = Path(td) / "test.md"
        md_file.write_text("---\nWhat is a gene?\nA unit of heredity.\n", encoding="utf-8")

        cards = parse_file(md_file)
        assert len(cards) == 1
        cards[0].card_id = "gene01"

        # Simulate sync flagging the card
        write_flags([(cards[0], "gene01")])
        assert "ADDED: What is a gene? %%gene01%%" in md_file.read_text(encoding="utf-8")

        # Register on undo stack
        register_sync_added_cards([cards[0]])
        assert get_undo_stack_depth() == 1
        assert get_redo_stack_depth() == 0

        # 1. Undo clears flags
        on_state_did_undo(SimpleNamespace(operation="VaultSync"))
        assert "ADDED" not in md_file.read_text(encoding="utf-8")
        assert get_undo_stack_depth() == 0
        assert get_redo_stack_depth() == 1

        # 2. Redo restores flags with original ID
        on_state_did_redo(SimpleNamespace(operation="VaultSync"))
        content_after_redo = md_file.read_text(encoding="utf-8")
        assert "ADDED: What is a gene? %%gene01%%" in content_after_redo
        assert get_undo_stack_depth() == 1
        assert get_redo_stack_depth() == 0


def test_new_sync_clears_redo_stack():
    clear_sync_undo_stack()
    c1 = SimpleNamespace(location="a.md:1", card_id="c1", file=Path("a.md"), line=1, first_question_line="q1")
    register_sync_added_cards([c1])

    # Pop onto redo stack via undo
    on_state_did_undo(SimpleNamespace(operation="VaultSync"))
    assert get_redo_stack_depth() == 1

    # New sync occurs
    c2 = SimpleNamespace(location="b.md:1", card_id="c2", file=Path("b.md"), line=1, first_question_line="q2")
    register_sync_added_cards([c2])

    # Redo stack must be cleared
    assert get_redo_stack_depth() == 0
    assert get_undo_stack_depth() == 1


def test_empty_sync_does_not_push_to_undo_stack():
    clear_sync_undo_stack()
    # Registering empty card list should do nothing
    register_sync_added_cards([])
    assert get_undo_stack_depth() == 0
    assert get_redo_stack_depth() == 0


def test_end_to_end_sync_undo_and_redo_cycle():
    clear_sync_undo_stack()
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        (vault_path / "m2a.toml").write_text('vault = "."\ndeck = "TestDeck"\n', encoding="utf-8")
        note_file = vault_path / "card.md"
        note_file.write_text("# Test\n---\nWhat is photosynthesis?\nLight to chemical energy.\n", encoding="utf-8")

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            # 1. Sync
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 1

            report = execute_sync_plan(col, plan)
            assert report.created_count == 1
            content = note_file.read_text(encoding="utf-8")
            assert "ADDED:" in content

            # 2. Attach redo hook
            attach_redo_hook(col)

            # 3. Undo
            undo_res = col.undo()
            assert undo_res.operation == "VaultSync"
            on_state_did_undo(undo_res)

            content_after_undo = note_file.read_text(encoding="utf-8")
            assert "ADDED:" not in content_after_undo
            assert get_redo_stack_depth() == 1

            # 4. Redo
            redo_res = col.redo()
            assert redo_res.operation == "VaultSync"

            content_after_redo = note_file.read_text(encoding="utf-8")
            assert "ADDED:" in content_after_redo
            assert get_redo_stack_depth() == 0
            assert get_undo_stack_depth() == 1
        finally:
            col.close()


def test_zero_mutation_sync_does_not_pollute_undo_stack():
    clear_sync_undo_stack()
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()
        (vault_path / "m2a.toml").write_text('vault = "."\ndeck = "TestDeck"\n', encoding="utf-8")

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            # User creates an initial note in Anki
            m = col.models.by_name("Basic")
            n = col.new_note(m)
            n["Front"] = "User Note"
            col.add_note(n, col.decks.all_names_and_ids()[0].id)
            assert col.undo_status().undo == "Add Note"

            # Sync with 0 changes runs
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 0
            assert plan.total_updates == 0

            report = execute_sync_plan(col, plan)
            assert report.created_count == 0
            assert report.updated_count == 0

            # The undo stack must NOT be polluted with an empty VaultSync entry!
            assert col.undo_status().undo == "Add Note"
            assert get_undo_stack_depth() == 0
        finally:
            col.close()


def test_attach_undo_hook_auto_unflags_card():
    clear_sync_undo_stack()
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()
        (vault_path / "m2a.toml").write_text('vault = "."\ndeck = "TestDeck"\n', encoding="utf-8")
        note_file = vault_path / "card.md"
        note_file.write_text("# Test\n---\nWhat is osmosis?\nWater diffusion.\n", encoding="utf-8")

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            plan = build_sync_plan(col, vault_path)
            report = execute_sync_plan(col, plan)
            assert report.created_count == 1
            assert "ADDED:" in note_file.read_text(encoding="utf-8")

            # col.undo() must trigger on_state_did_undo via the hook automatically
            undo_res = col.undo()
            assert undo_res.operation == "VaultSync"

            # Verify that the card was unflagged without calling on_state_did_undo manually
            content = note_file.read_text(encoding="utf-8")
            assert "ADDED:" not in content
            assert "What is osmosis?" in content
            assert get_undo_stack_depth() == 0
            assert get_redo_stack_depth() == 1
        finally:
            col.close()


def test_undo_redo_dedup_tokens():
    clear_sync_undo_stack()
    c1 = SimpleNamespace(location="a.md:1", card_id="c1", file=Path("a.md"), line=1, first_question_line="q1")
    register_sync_added_cards([c1])
    assert get_undo_stack_depth() == 1

    changes = SimpleNamespace(operation="VaultSync", counter=42)
    # First invocation
    on_state_did_undo(changes)
    assert get_undo_stack_depth() == 0
    assert get_redo_stack_depth() == 1

    # Second invocation with same token must be ignored
    on_state_did_undo(changes)
    assert get_undo_stack_depth() == 0
    assert get_redo_stack_depth() == 1


def test_large_sync_with_occlusions_and_suspension_merges_atomically():
    """Verify that syncing more than 30 cards (which exceeds Anki backend ring buffer)
    with card suspension still merges atomically into a single VaultSync undo step."""
    clear_sync_undo_stack()
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()
        img = vault_path / "img.png"
        img.write_bytes(b"image-data")
        (vault_path / "m2a.toml").write_text('vault = "."\ndeck = "TestDeck"\nimage_occlusion_mode = "native"\nsuspend_new_occlusions = true\n', encoding="utf-8")

        # Create 40 notes (exceeding standard 25-step undo ring buffer)
        lines = []
        for i in range(40):
            lines.append(f"---\nImage Cloze: IO Question {i}\n![[img.png]]\n")
        (vault_path / "ios.md").write_text("\n".join(lines), encoding="utf-8")

        col = Collection(str(tmp_path / "col.anki2"))
        try:
            plan = build_sync_plan(col, vault_path)
            assert plan.total_creates == 40

            report = execute_sync_plan(col, plan)
            assert report.created_count == 40
            assert report.suspended_count == 40

            # MUST be single atomic VaultSync step, NOT Suspend or Add Note!
            status = col.undo_status()
            assert status.undo == "VaultSync"
            assert get_undo_stack_depth() == 1

            # Trigger undo: all 40 cards must be undone and unflagged in one step
            undo_res = col.undo()
            assert undo_res.operation == "VaultSync"
            assert get_undo_stack_depth() == 0
            assert get_redo_stack_depth() == 1

            # Markdown must have all ADDED flags removed
            content = (vault_path / "ios.md").read_text(encoding="utf-8")
            assert "ADDED:" not in content
        finally:
            col.close()
