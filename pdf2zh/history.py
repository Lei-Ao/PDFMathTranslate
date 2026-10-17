"""Completed translations, with read-only discovery of legacy file pairs.

A task becomes visible only after record.json is atomically committed. Invalid
records are skipped independently, never renamed, repaired or deleted by reads.
No PDF contents or translator configuration are read or saved here.
"""

import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path, PureWindowsPath
from uuid import UUID

from pdf2zh.runtime_paths import (
    create_translation_dir,
    get_translations_dir,
    legacy_translation_locations,
)

__all__ = [
    "create_translation_dir", "record_completed", "list_translations", "get_translation"
]

_RECORD_LIMIT = 64 * 1024
_INVALID = (OSError, ValueError, TypeError, OverflowError, RecursionError, RuntimeError)


def _file(path: Path) -> Path:
    path = path.resolve(strict=True)
    if path.suffix.lower() != ".pdf" or not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Translation files must be existing, nonempty PDF files")
    return path


def _relative_file(directory: Path, value: str) -> Path:
    # Check Windows syntax on every OS as records can be copied across platforms.
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Invalid relative file path")
    path = Path(value)
    if path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts:
        raise ValueError("Record paths must stay inside their translation directory")
    resolved = _file(directory / path)
    resolved.relative_to(directory)
    return resolved


def _timestamp(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Invalid completion timestamp")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise ValueError("Invalid completion timestamp")
    return value


def _distinct(original: Path, translated: Path, dual: Path | None) -> None:
    files = [original, translated] + ([dual] if dual else [])
    if len(set(files)) != len(files):
        raise ValueError("Original and translated files must have distinct paths")


def _public(record_id, original, translated, dual, completed_at, location) -> dict:
    return {
        "id": record_id,
        "name": original.name,
        "original": str(original),
        "translated": str(translated),
        "dual": str(dual) if dual else None,
        "completed_at": float(completed_at),
        "location": location,
    }


def record_completed(
    original: Path, translated: Path, dual: Path | None = None
) -> dict:
    """Commit a successful task; inputs must belong to its UUID directory.

    Raises ValueError/OSError on invalid or unavailable files. No record is
    published on failure. Public paths are absolute strings; on disk they are
    relative to the task directory so the complete data root remains portable.
    """
    root = get_translations_dir().resolve()
    original = _file(Path(original))
    translated = _file(Path(translated))
    dual = _file(Path(dual)) if dual is not None else None
    relative = original.relative_to(root)
    if len(relative.parts) < 2:
        raise ValueError("Files must belong to a translation task")
    record_id = relative.parts[0]
    if str(UUID(record_id)) != record_id:
        raise ValueError("Translation directory must use a canonical UUID")
    directory = root / record_id
    paths = {}
    for key, path in (("original", original), ("translated", translated), ("dual", dual)):
        paths[key] = path.relative_to(directory).as_posix() if path else None
        if path:
            _relative_file(directory, paths[key])
    _distinct(original, translated, dual)
    completed_at = time.time()
    payload = {
        "version": 1,
        "status": "completed",
        "id": record_id,
        "completed_at": completed_at,
        **paths,
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory,
            prefix=".record-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / "record.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return _public(
        record_id, original, translated, dual, completed_at, "翻译记录"
    )


def _read_record(directory: Path) -> dict:
    record_file = directory / "record.json"
    # A record symlink must not make the history reader access outside files.
    record_file.resolve(strict=True).relative_to(directory)
    with record_file.open("rb") as handle:
        raw = handle.read(_RECORD_LIMIT + 1)
    if len(raw) > _RECORD_LIMIT:
        raise ValueError("Record is too large")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Record must be an object")
    if payload.get("version") != 1 or payload.get("status") != "completed":
        raise ValueError("Not a supported completed record")
    record_id = directory.name
    if str(UUID(record_id)) != record_id or payload.get("id") != record_id:
        raise ValueError("Record ID does not match its directory")
    original = _relative_file(directory, payload.get("original"))
    translated = _relative_file(directory, payload.get("translated"))
    dual = payload.get("dual")
    dual = _relative_file(directory, dual) if dual is not None else None
    _distinct(original, translated, dual)
    return _public(
        record_id, original, translated, dual,
        _timestamp(payload.get("completed_at")), "翻译记录",
    )


def _children(directory: Path) -> list[Path]:
    try:
        return sorted(directory.iterdir())
    except OSError:
        return []


def _legacy_records(directory: Path, location: str):
    entries = _children(directory)
    names = {path.name.casefold(): path for path in entries}
    for target in entries:
        if not target.name.lower().endswith("-mono.pdf"):
            continue
        try:
            source_name = target.name[:-len("-mono.pdf")]
            source = names.get((source_name + ".pdf").casefold())
            if source is None:
                continue
            original = _file(source)
            translated = _file(target)
            # Legacy discovery is restricted to pairs physically in this directory.
            original.relative_to(directory)
            translated.relative_to(directory)
            dual = names.get((source_name + "-dual.pdf").casefold())
            if dual is not None:
                try:
                    dual = _file(dual)
                    dual.relative_to(directory)
                except _INVALID:
                    dual = None
            _distinct(original, translated, dual)
            key = "\0".join(_path_key(path) for path in (original, translated))
            record_id = "legacy-" + hashlib.sha256(key.encode("utf-8")).hexdigest()
            yield _public(
                record_id, original, translated, dual,
                _timestamp(translated.stat().st_mtime), location,
            )
        except _INVALID:
            continue


def _path_key(path) -> str:
    return os.path.normcase(str(path))


def list_translations() -> list[dict]:
    """Discover completed tasks and legacy pairs; isolate each unreadable entry.

    Reads never create directories or mutate damaged files. Deduplication uses
    resolved original/mono paths (not document names), preferring new records.
    """
    records = []
    root = get_translations_dir().resolve()
    for directory in _children(root):
        try:
            # Only direct task directories, including no symlink escapes.
            directory = directory.resolve(strict=True)
            if directory.parent != root or not directory.is_dir():
                continue
            records.append(_read_record(directory))
        except _INVALID:
            continue
    for directory, location in legacy_translation_locations():
        try:
            records.extend(_legacy_records(directory.resolve(), location))
        except _INVALID:
            continue
    unique = {}
    for record in records:
        key = (_path_key(record["original"]), _path_key(record["translated"]))
        unique.setdefault(key, record)
    return sorted(
        unique.values(), key=lambda item: (-item["completed_at"], item["id"])
    )


def get_translation(record_id: str) -> dict | None:
    """Resolve only discovered IDs; arbitrary input is never used as a path."""
    return next(
        (record for record in list_translations() if record["id"] == record_id), None
    )
