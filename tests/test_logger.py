from __future__ import annotations

from pathlib import Path
from src import logger


def test_logger_lifecycle(tmp_path: Path):
    log_file = tmp_path / "test_lifecycle.log"
    logger.set_custom_log_path(log_file)

    try:
        assert logger.read_log_entries() == []
        assert logger.read_log_text() == ""

        logger.log_info("First event")
        logger.log_warning("Second warning event")
        logger.log_error("Third error event")

        entries = logger.read_log_entries(newest_first=True)
        assert len(entries) == 3
        # Newest first:
        assert "Third error event" in entries[0]
        assert "[ERROR]" in entries[0]
        assert "Second warning event" in entries[1]
        assert "[WARNING]" in entries[1]
        assert "First event" in entries[2]
        assert "[INFO]" in entries[2]

        # Chronological order
        chrono = logger.read_log_entries(newest_first=False)
        assert "First event" in chrono[0]
        assert "Third error event" in chrono[2]

        # Clear log
        logger.clear_log()
        assert logger.read_log_entries() == []
        assert logger.read_log_text() == ""
    finally:
        logger.set_custom_log_path(None)


def test_logger_trimming(tmp_path: Path):
    log_file = tmp_path / "test_trim.log"
    logger.set_custom_log_path(log_file)

    try:
        # Write 2050 lines
        for i in range(2050):
            logger.log_info(f"Entry {i}")

        lines = log_file.read_text(encoding="utf-8").splitlines()
        # Should have trimmed down to 1000 lines
        assert len(lines) == 1049
        # Older entries (0..1000) trimmed away
        assert "Entry 1001" in lines[0]
        assert "Entry 2049" in lines[-1]
    finally:
        logger.set_custom_log_path(None)
