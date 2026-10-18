from __future__ import annotations

import datetime
from pathlib import Path

try:
    import aqt
    from aqt import mw
except ImportError:
    aqt = None
    mw = None

_custom_log_path: Path | None = None
MAX_LOG_LINES = 2000
TRIM_LOG_LINES = 1000


def set_custom_log_path(path: Path | None) -> None:
    global _custom_log_path
    _custom_log_path = path


def get_log_file_path() -> Path:
    if _custom_log_path is not None:
        return _custom_log_path

    # Try Anki profile directory
    live_mw = aqt.mw if (aqt is not None and getattr(aqt, "mw", None) is not None) else mw
    if live_mw is not None and hasattr(live_mw, "pm") and live_mw.pm is not None:
        try:
            folder = Path(live_mw.pm.profileFolder())
            if folder.is_dir():
                return folder / "vaultsync.log"
        except Exception:
            pass

    # Try addon root directory
    addon_dir = Path(__file__).resolve().parent.parent
    try:
        test_file = addon_dir / ".write_test"
        test_file.touch()
        test_file.unlink()
        return addon_dir / "vaultsync.log"
    except Exception:
        # Fallback to user home directory
        fallback = Path.home() / ".anki_vaultsync"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback / "vaultsync.log"


def log(message: str, level: str = "INFO") -> None:
    log_file = get_log_file_path()
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] [{level.upper()}] {message}\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)
        _maybe_trim(log_file)
    except Exception:
        pass


def _maybe_trim(log_file: Path) -> None:
    try:
        if not log_file.is_file():
            return
        lines = log_file.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) > MAX_LOG_LINES:
            kept = lines[-TRIM_LOG_LINES:]
            log_file.write_text("".join(kept), encoding="utf-8")
    except Exception:
        pass


def log_info(message: str) -> None:
    log(message, level="INFO")


def log_warning(message: str) -> None:
    log(message, level="WARNING")


def log_error(message: str) -> None:
    log(message, level="ERROR")


def read_log_entries(newest_first: bool = True, max_lines: int | None = None) -> list[str]:
    log_file = get_log_file_path()
    if not log_file.is_file():
        return []
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines()
        if newest_first:
            lines.reverse()
        if max_lines is not None:
            lines = lines[:max_lines]
        return lines
    except Exception:
        return []


def read_log_text(newest_first: bool = True, max_lines: int | None = None) -> str:
    entries = read_log_entries(newest_first=newest_first, max_lines=max_lines)
    return "\n".join(entries)


def clear_log() -> None:
    log_file = get_log_file_path()
    if log_file.is_file():
        try:
            log_file.write_text("", encoding="utf-8")
        except Exception:
            pass
