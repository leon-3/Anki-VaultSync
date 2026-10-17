from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from anki.collection import Collection
from markdown2anki.flags import write_flags
from markdown2anki.parser import parse_file

from src.contracts import SyncPlan
from src.undo import (
    clear_sync_undo_stack,
    get_undo_stack_depth,
    on_state_did_undo,
    pop_last_sync_cards,
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
