"""Data locations shared by source, installed and frozen applications.

Resolving a location never creates it. Only starting a translation writes data.
"""

import os
import sys
from pathlib import Path
from uuid import uuid4


# Capture once so later chdir calls never change legacy discovery.
_STARTUP_CWD = Path.cwd().resolve()


def _source_root() -> Path | None:
    if getattr(sys, "frozen", False):
        return None
    root = Path(__file__).resolve().parent.parent
    if (root / "pyproject.toml").is_file() and (root / "pdf2zh").is_dir():
        return root
    return None


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def _user_data_dir() -> Path:
    if sys.platform == "win32":
        return _local_app_data() / "PDFMathTranslate" / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PDFMathTranslate" / "data"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "PDFMathTranslate" / "data"


def get_data_dir() -> Path:
    """Return an absolute data root, honoring PDF2ZH_DATA_DIR first."""
    override = os.environ.get("PDF2ZH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    source = _source_root()
    if source is not None:
        return source.parent / "data"
    return _user_data_dir().resolve()


def get_translations_dir() -> Path:
    return get_data_dir() / "translations"


def legacy_translation_locations() -> list[tuple[Path, str]]:
    """Explicit read-only migration candidates; never search arbitrary cwd trees."""
    candidates = []
    source = _source_root()
    if source is not None:
        candidates.extend([
            (source.parent / "pdf2zh_files", "旧版：工作区"),
            (source / "pdf2zh_files", "旧版：源码目录"),
        ])
    local = _local_app_data()
    candidates.extend([
        (local / "PDFMathTranslate" / "pdf2zh_files", "旧版：用户目录"),
        (local / "pdf2zh_files", "旧版：LocalAppData"),
    ])
    candidates.append((_STARTUP_CWD / "pdf2zh_files", "旧版：启动目录"))
    result = []
    seen = set()
    for path, label in candidates:
        try:
            path = path.resolve()
        except (OSError, RuntimeError):
            continue
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append((path, label))
    return result


def get_legacy_translation_dirs() -> list[Path]:
    return [path for path, _ in legacy_translation_locations()]


def create_translation_dir() -> Path:
    """Create a private, collision-resistant directory for one translation."""
    root = get_translations_dir()
    root.mkdir(parents=True, exist_ok=True)
    while True:
        directory = root / str(uuid4())
        try:
            directory.mkdir()
        except FileExistsError:
            continue
        return directory
