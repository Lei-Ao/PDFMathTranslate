"""History tests use synthetic files only, never existing user documents."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from pdf2zh import history, runtime_paths


class RuntimePathsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.environment = patch.dict(os.environ, {
            "USERPROFILE": str(self.root), "LOCALAPPDATA": str(self.root / "local")
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_source_override_and_user_install_locations(self):
        source = self.root / "workspace" / "PDFMathTranslate"
        with patch.object(runtime_paths, "_source_root", return_value=source):
            self.assertEqual(runtime_paths.get_data_dir(), source.parent / "data")
            with patch.dict(os.environ, {"PDF2ZH_DATA_DIR": str(self.root / "override")}):
                self.assertEqual(runtime_paths.get_data_dir(), self.root / "override")
        with patch.object(runtime_paths, "_source_root", return_value=None):
            with patch.object(sys, "platform", "win32"):
                with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local")}):
                    self.assertEqual(
                        runtime_paths.get_data_dir(),
                        self.root / "local" / "PDFMathTranslate" / "data",
                    )
            with patch.object(sys, "platform", "linux"):
                with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "xdg")}):
                    self.assertEqual(
                        runtime_paths.get_data_dir(),
                        self.root / "xdg" / "PDFMathTranslate" / "data",
                    )
            with patch.object(sys, "platform", "darwin"):
                with patch.object(Path, "home", return_value=self.root):
                    self.assertEqual(
                        runtime_paths.get_data_dir(),
                        self.root / "Library" / "Application Support" / "PDFMathTranslate" / "data",
                    )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_source_detection_and_frozen_detection(self):
        source = self.root / "source"
        module = source / "pdf2zh" / "runtime_paths.py"
        module.parent.mkdir(parents=True)
        with patch.object(runtime_paths, "__file__", str(module)):
            with patch.object(sys, "frozen", False, create=True):
                self.assertIsNone(runtime_paths._source_root())
                (source / "pyproject.toml").touch()
                self.assertEqual(runtime_paths._source_root(), source)
            with patch.object(sys, "frozen", True, create=True):
                self.assertIsNone(runtime_paths._source_root())

    def test_explicit_legacy_candidates_and_frozen_startup_directory(self):
        source = self.root / "workspace" / "source"
        local = self.root / "local"
        startup = self.root / "startup"
        with patch.object(runtime_paths, "_source_root", return_value=source):
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                locations = runtime_paths.get_legacy_translation_dirs()
                self.assertIn(source.parent / "pdf2zh_files", locations)
                self.assertIn(source / "pdf2zh_files", locations)
                self.assertIn(local / "pdf2zh_files", locations)
                self.assertIn(local / "PDFMathTranslate" / "pdf2zh_files", locations)
        with patch.object(runtime_paths, "_source_root", return_value=None):
            with patch.object(sys, "frozen", True, create=True):
                with patch.object(runtime_paths, "_STARTUP_CWD", startup):
                    self.assertIn(
                        startup / "pdf2zh_files", runtime_paths.get_legacy_translation_dirs()
                    )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_fresh_import_and_empty_listing_have_no_filesystem_side_effects(self):
        data = self.root / "not-created"
        environment = dict(os.environ, PDF2ZH_DATA_DIR=str(data))
        environment["PYTHONPATH"] = str(Path(runtime_paths.__file__).resolve().parent.parent)
        result = subprocess.run(
            [sys.executable, "-c", (
                "from pdf2zh import runtime_paths, history; "
                "runtime_paths._source_root = lambda: None; "
                "runtime_paths.get_data_dir(); "
                "runtime_paths.get_legacy_translation_dirs(); "
                "assert history.list_translations() == []"
            )],
            cwd=self.root, env=environment, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(data.exists())


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        environment = patch.dict(os.environ, {"PDF2ZH_DATA_DIR": str(self.root / "data")})
        environment.start()
        self.addCleanup(environment.stop)
        self.legacy = self.root / "legacy"
        locations = patch.object(
            history, "legacy_translation_locations",
            return_value=[(self.legacy, "旧版：测试")],
        )
        locations.start()
        self.addCleanup(locations.stop)

    def pair(self, directory, name="paper", dual=False):
        directory.mkdir(parents=True, exist_ok=True)
        files = [directory / f"{name}.pdf", directory / f"{name}-mono.pdf"]
        if dual:
            files.append(directory / f"{name}-dual.pdf")
        for path in files:
            path.write_bytes(b"%PDF-1.7\nsynthetic test placeholder\n")
        return (*files[:2], files[2] if dual else None)

    def completed(self, name="paper", dual=False, timestamp=100.5):
        directory = history.create_translation_dir()
        files = self.pair(directory, name, dual)
        with patch.object(history.time, "time", return_value=timestamp):
            record = history.record_completed(*files)
        return directory, files, record

    def test_uuid_directories_are_unique_under_concurrent_creation(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            directories = list(pool.map(lambda _: history.create_translation_dir(), range(32)))
        self.assertEqual(len(set(directories)), 32)
        for directory in directories:
            self.assertEqual(directory.parent, runtime_paths.get_translations_dir())
            self.assertEqual(UUID(directory.name).version, 4)
            self.assertTrue(directory.is_dir())
            self.assertEqual(list(directory.iterdir()), [])
        self.assertEqual(history.list_translations(), [])

    def test_atomic_record_round_trip_and_whitelisted_portable_metadata(self):
        directory, files, record = self.completed(name="同名论文", dual=True)
        payload = json.loads((directory / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {
            "version", "status", "id", "completed_at", "original", "translated", "dual"
        })
        self.assertEqual(payload["original"], files[0].name)
        self.assertNotIn(str(self.root), json.dumps(payload))
        self.assertEqual(set(record), {
            "id", "name", "original", "translated", "dual", "completed_at", "location"
        })
        self.assertIsInstance(record["completed_at"], float)
        self.assertEqual(record["completed_at"], 100.5)
        self.assertEqual(record["location"], "翻译记录")
        for key in ("original", "translated", "dual"):
            self.assertIsInstance(record[key], str)
            self.assertTrue(Path(record[key]).is_absolute())
        self.assertEqual(history.list_translations(), [record])
        self.assertEqual(history.get_translation(record["id"]), record)
        self.assertIsNone(history.get_translation("../record.json"))
        self.assertEqual(sorted(path.name for path in directory.iterdir()),
                         sorted([path.name for path in files] + ["record.json"]))
        # A fresh interpreter must discover records without a process-local cache.
        code = (
            "import json; from pdf2zh.history import get_translation; "
            f"print(json.dumps(get_translation({record['id']!r})))"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(runtime_paths.__file__).resolve().parent.parent)
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=self.root, env=environment,
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), record)

    def test_portable_record_survives_moving_the_data_root(self):
        _, _, record = self.completed(dual=True)
        old_root = runtime_paths.get_data_dir()
        new_root = self.root / "relocated-data"
        old_root.rename(new_root)
        with patch.dict(os.environ, {"PDF2ZH_DATA_DIR": str(new_root)}):
            loaded = history.get_translation(record["id"])
        self.assertEqual(loaded["id"], record["id"])
        for key in ("original", "translated", "dual"):
            self.assertEqual(
                Path(loaded[key]).relative_to(new_root),
                Path(record[key]).relative_to(old_root),
            )

    def test_completion_is_invisible_until_atomic_replace(self):
        directory = history.create_translation_dir()
        files = self.pair(directory)
        replace = os.replace
        observed = []

        def observe_replace(source, destination):
            observed.append(history.list_translations())
            self.assertTrue(Path(source).is_file())
            self.assertFalse(Path(destination).exists())
            replace(source, destination)

        with patch.object(history.os, "replace", side_effect=observe_replace):
            record = history.record_completed(*files)
        self.assertEqual(observed, [[]])
        self.assertEqual(history.list_translations(), [record])

    def test_uuid_collision_is_retried_without_reusing_a_directory(self):
        existing = history.create_translation_dir()
        next_id = uuid4()
        with patch.object(runtime_paths, "uuid4", side_effect=[UUID(existing.name), next_id]):
            created = history.create_translation_dir()
        self.assertNotEqual(created, existing)
        self.assertEqual(created.name, str(next_id))
        self.assertTrue(existing.is_dir())

    def test_no_dual_and_nested_relative_paths(self):
        directory = history.create_translation_dir()
        original, translated, _ = self.pair(directory / "nested")
        record = history.record_completed(original, translated)
        self.assertIsNone(record["dual"])
        self.assertEqual(history.list_translations(), [record])
        self.assertEqual(
            json.loads((directory / "record.json").read_text())["original"],
            "nested/paper.pdf",
        )

    def test_failed_and_unrecorded_jobs_are_invisible(self):
        self.pair(history.create_translation_dir(), dual=True)
        self.pair(self.legacy, "unfinished")
        (self.legacy / "unfinished.pdf").unlink()
        (self.legacy / "only-original.pdf").write_bytes(b"placeholder")
        self.assertEqual(history.list_translations(), [])
        (self.legacy / "unfinished.pdf").write_bytes(b"placeholder")
        self.assertEqual(len(history.list_translations()), 1)

    def test_atomic_replace_failure_preserves_old_record_and_cleans_temporary(self):
        directory, files, previous = self.completed()
        before = (directory / "record.json").read_bytes()
        with patch.object(history.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                history.record_completed(*files)
        self.assertEqual((directory / "record.json").read_bytes(), before)
        self.assertEqual(history.list_translations(), [previous])
        self.assertEqual(list(directory.glob("*.tmp")), [])
        empty = history.create_translation_dir()
        with patch.object(history.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                history.record_completed(*self.pair(empty))
        self.assertFalse((empty / "record.json").exists())
        self.assertEqual(history.list_translations(), [previous])

    def test_writer_rejects_cross_task_missing_empty_and_non_pdf_files(self):
        directory = history.create_translation_dir()
        original, translated, _ = self.pair(directory)
        outside, _, _ = self.pair(self.root / "outside")
        other, _, _ = self.pair(history.create_translation_dir())
        for args in ((outside, translated), (original, outside), (original, other),
                     (original, original), (original, translated, outside)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                history.record_completed(*args)
        empty = directory / "empty.pdf"
        empty.touch()
        text = directory / "config.json"
        text.write_text("{}")
        for invalid in (empty, text, directory / "missing.pdf"):
            with self.subTest(invalid=invalid), self.assertRaises((ValueError, OSError)):
                history.record_completed(original, invalid)
        self.assertFalse((directory / "record.json").exists())

    def test_legacy_pairs_optional_dual_no_name_dedup_and_time_order(self):
        first = self.pair(self.legacy, dual=True)
        second_dir = self.root / "legacy2"
        second = self.pair(second_dir)
        os.utime(first[1], (200, 200))
        os.utime(second[1], (300, 300))
        _, _, completed = self.completed(timestamp=250)
        # Duplicate candidates and a new task alias must not create extra rows.
        locations = [(self.legacy, "旧版：测试"), (self.legacy, "别名"),
                     (second_dir, "旧版：另一个目录"), (Path(completed["original"]).parent, "别名")]
        before = {path: path.read_bytes() for path in (*first, *second[:2])}
        with patch.object(history, "legacy_translation_locations", return_value=locations):
            records = history.list_translations()
            self.assertEqual([item["completed_at"] for item in records], [300.0, 250.0, 200.0])
            self.assertEqual(len({item["id"] for item in records}), 3)
            self.assertEqual([item["name"] for item in records], ["paper.pdf"] * 3)
            self.assertEqual(records[1], completed)
            self.assertIsNone(records[0]["dual"])
            self.assertEqual(records[2]["dual"], str(first[2]))
            self.assertEqual(history.get_translation(records[2]["id"]), records[2])
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse((self.legacy / "record.json").exists())

    def test_corrupt_and_unsafe_records_are_independently_isolated(self):
        _, _, valid = self.completed()
        outside, _, _ = self.pair(self.root / "outside")
        bad_values = ["../outside/paper.pdf", str(outside), "C:/outside/paper.pdf",
                      "C:paper.pdf", "..\\outside\\paper.pdf", None, 123]
        payloads = [[], None, {"status": "failed"}]
        for value in bad_values:
            payloads.append({"original": value})
        for value in ("yesterday", float("nan"), float("inf"), -1, True, 10**100):
            payloads.append({"completed_at": value})
        payloads.extend([{"id": str(uuid4())}, {"status": "failed"}, {"version": 99},
                         {"translated": "missing.pdf"}, {"dual": "missing-dual.pdf"},
                         {"translated": "paper.pdf"}])
        for changes in payloads:
            directory = history.create_translation_dir()
            self.pair(directory)
            payload = {"version": 1, "status": "completed", "id": directory.name,
                       "completed_at": 100, "original": "paper.pdf",
                       "translated": "paper-mono.pdf", "dual": None}
            if isinstance(changes, dict):
                payload.update(changes)
            else:
                payload = changes
            (directory / "record.json").write_text(json.dumps(payload), encoding="utf-8")
        for raw in (b"{broken", b"\xff\xfeinvalid", b" " * (history._RECORD_LIMIT + 1)):
            directory = history.create_translation_dir()
            (directory / "record.json").write_bytes(raw)
        self.assertEqual(history.list_translations(), [valid])

    def test_deleted_files_and_unreadable_directory_do_not_hide_other_records(self):
        _, _, good = self.completed(name="good")
        _, files, _ = self.completed(name="deleted")
        files[1].unlink()
        with patch.object(Path, "iterdir", side_effect=PermissionError("not readable")):
            self.assertEqual(history.list_translations(), [])
        self.assertEqual(history.list_translations(), [good])

    def test_unreadable_record_is_isolated_and_pdf_contents_are_never_opened(self):
        _, _, good = self.completed(name="readable")
        bad_dir, _, _ = self.completed(name="unreadable")
        original_open = Path.open
        accessed = []

        def guarded_open(path, *args, **kwargs):
            accessed.append(path)
            self.assertEqual(path.name, "record.json")
            if path == bad_dir / "record.json":
                raise PermissionError("blocked record")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open):
            self.assertEqual(history.list_translations(), [good])
        self.assertEqual(len(accessed), 2)

    def test_legacy_discovery_reads_metadata_without_opening_pdf_contents(self):
        files = self.pair(self.legacy, dual=True)
        with patch.object(Path, "open", side_effect=AssertionError("PDF content read")):
            records = history.list_translations()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["original"], str(files[0]))

    def test_legacy_invalid_optional_dual_and_uppercase_suffix(self):
        original, translated, dual = self.pair(self.legacy, dual=True)
        dual.write_bytes(b"")
        translated = translated.rename(translated.with_suffix(".PDF"))
        rows = history.list_translations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["original"], str(original))
        self.assertEqual(rows[0]["translated"], str(translated))
        self.assertIsNone(rows[0]["dual"])
        original.write_bytes(b"")
        self.assertEqual(history.list_translations(), [])

    def test_symlink_escapes_are_rejected_when_supported(self):
        outside, _, _ = self.pair(self.root / "outside")
        directory = history.create_translation_dir()
        original, translated, _ = self.pair(directory)
        original.unlink()
        try:
            original.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        with self.assertRaises(ValueError):
            history.record_completed(original, translated)
        payload = {"version": 1, "status": "completed", "id": directory.name,
                   "completed_at": 100, "original": original.name,
                   "translated": translated.name, "dual": None}
        (directory / "record.json").write_text(json.dumps(payload))
        self.assertEqual(history.list_translations(), [])


if __name__ == "__main__":
    unittest.main()
