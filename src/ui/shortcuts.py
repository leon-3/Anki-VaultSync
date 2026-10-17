from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aqt import mw
from aqt.qt import QAction, QKeySequence, QShortcut, sip


def _is_deleted(obj: Any) -> bool:
    if obj is None:
        return True
    if sip is not None and hasattr(sip, "isdeleted"):
        try:
            return sip.isdeleted(obj)
        except Exception:
            return True
    return False


def normalize_key_sequence(seq_or_str: QKeySequence | str) -> str:
    """Returns a canonical portable string representation of a shortcut (e.g. 'Ctrl+Alt+V')."""
    if isinstance(seq_or_str, str):
        seq = QKeySequence(seq_or_str.strip())
    else:
        seq = seq_or_str
    if seq.isEmpty():
        return ""
    return seq.toString(QKeySequence.SequenceFormat.PortableText)


def find_shortcut_conflict(
    shortcut_str: str,
    allowed_actions: set[QAction] | None = None,
) -> str | None:
    """Checks whether the given shortcut string is already registered on mw or any child action.

    Returns the descriptive name/label of the conflicting action, or None if no conflict exists.
    """
    clean_str = shortcut_str.strip()
    if not clean_str:
        return None

    target_seq = QKeySequence(clean_str)
    if target_seq.isEmpty():
        return None

    target_canonical = target_seq.toString(QKeySequence.SequenceFormat.PortableText).casefold()

    if mw is None:
        return None

    # Check QAction objects under mw
    if hasattr(mw, "findChildren"):
        for action in mw.findChildren(QAction):
            if _is_deleted(action):
                continue
            if allowed_actions and action in allowed_actions:
                continue

            try:
                # Check primary shortcut
                sc = action.shortcut()
                if not sc.isEmpty():
                    if sc.toString(QKeySequence.SequenceFormat.PortableText).casefold() == target_canonical:
                        label = action.text().split("\t")[0].replace("&", "").strip() or action.objectName() or "Anki action"
                        return label

                # Check secondary / multiple shortcuts if any
                for sc_extra in action.shortcuts():
                    if not sc_extra.isEmpty():
                        if sc_extra.toString(QKeySequence.SequenceFormat.PortableText).casefold() == target_canonical:
                            label = action.text().split("\t")[0].replace("&", "").strip() or action.objectName() or "Anki action"
                            return label
            except RuntimeError:
                continue

        # Check QShortcut objects under mw
        for qsc in mw.findChildren(QShortcut):
            if _is_deleted(qsc):
                continue
            try:
                sc = qsc.key()
                if not sc.isEmpty():
                    if sc.toString(QKeySequence.SequenceFormat.PortableText).casefold() == target_canonical:
                        label = qsc.objectName() or "Anki shortcut"
                        return label
            except RuntimeError:
                continue

    return None
