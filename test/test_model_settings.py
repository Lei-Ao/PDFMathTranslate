import json
import tempfile
import unittest
from pathlib import Path
from threading import RLock

from pdf2zh.config import ConfigManager
from pdf2zh.model_settings import is_secret, load_service_settings, save_service_settings


class Translator:
    name = "test-openai"
    envs = {"OPENAI_BASE_URL": "https://example.test/v1", "OPENAI_API_KEY": None,
            "OPENAI_MODEL": "default-model", "OPENAI_STREAM": "true",
            "OPENAI_STOP_TOKENS": "", "OPENAI_MAX_TOKENS": -1}


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = ConfigManager._instance
        store = ConfigManager.__new__(ConfigManager)
        store._config_path = Path(self.temp.name) / "config.json"
        store._config_data = {}
        store._lock = RLock()
        store._initialized = True
        ConfigManager._instance = store
        self.store = store
        self.values = dict(Translator.envs, OPENAI_API_KEY="test-key-only", OPENAI_MODEL="my-model")

    def tearDown(self):
        ConfigManager._instance = self.previous
        self.temp.cleanup()

    def test_save_and_reload_without_translation(self):
        save_service_settings("OpenAI", Translator, self.values)
        self.store._config_data = json.loads(self.store._config_path.read_text(encoding="utf-8"))
        self.assertEqual(ConfigManager.get("PDF2ZH_SERVICE"), "OpenAI")
        self.assertEqual(load_service_settings(Translator)["OPENAI_API_KEY"], "test-key-only")
        self.assertEqual(load_service_settings(Translator)["OPENAI_MODEL"], "my-model")
        self.assertEqual(load_service_settings(Translator)["OPENAI_STOP_TOKENS"], "")

    def test_invalid_edit_does_not_overwrite_saved_settings(self):
        save_service_settings("OpenAI", Translator, self.values)
        original = self.store._config_path.read_bytes()
        for changes in ({"OPENAI_MODEL": ""}, {"OPENAI_API_KEY": ""},
                        {"OPENAI_BASE_URL": "wrong"}, {"OPENAI_STREAM": "yes"},
                        {"OPENAI_MAX_TOKENS": "zero"}):
            with self.assertRaises(ValueError):
                save_service_settings("OpenAI", Translator, dict(self.values, **changes))
            self.assertEqual(self.store._config_path.read_bytes(), original)

    def test_repeated_config_values_are_not_removed_as_cycles(self):
        ConfigManager.set("repeated", {"first": "same", "second": "same", "blank1": "", "blank2": ""})
        saved = json.loads(self.store._config_path.read_text(encoding="utf-8"))["repeated"]
        self.assertEqual(saved, {"first": "same", "second": "same", "blank1": "", "blank2": ""})
        self.assertTrue(is_secret("OPENAI_API_KEY"))
        self.assertTrue(is_secret("TENCENTCLOUD_SECRET_ID"))
        self.assertFalse(is_secret("OPENAI_STOP_TOKENS"))


if __name__ == "__main__":
    unittest.main()
