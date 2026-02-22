from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sidecue.config import DEFAULT_CONFIG, load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_does_not_mutate_defaults_or_cross_pollute_paths(self) -> None:
        default_paths_before = list(DEFAULT_CONFIG["documents"]["paths"])

        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            config_path_1 = Path(tmp1) / "config.toml"
            config_path_2 = Path(tmp2) / "config.toml"
            config_path_1.write_text("", encoding="utf-8")
            config_path_2.write_text("", encoding="utf-8")

            config_1 = load_config(config_path_1)
            config_2 = load_config(config_path_2)

        expected_path_1 = str((config_path_1.parent / "knowledge").resolve())
        expected_path_2 = str((config_path_2.parent / "knowledge").resolve())

        self.assertEqual(config_1.documents.paths, [expected_path_1])
        self.assertEqual(config_2.documents.paths, [expected_path_2])
        self.assertEqual(DEFAULT_CONFIG["documents"]["paths"], default_paths_before)

    def test_new_asr_config_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text("", encoding="utf-8")
            config = load_config(config_path)

        self.assertEqual(config.asr.mode, "stdin")
        self.assertEqual(config.asr.language, "en-US")

    def test_new_llm_config_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text("", encoding="utf-8")
            config = load_config(config_path)

        self.assertEqual(config.llm.provider, "codex")
        self.assertEqual(config.llm.model, "gpt-5.3-codex-spark")
        self.assertEqual(config.llm.reasoning_effort, "low")
        self.assertEqual(config.llm.timeout_seconds, 30.0)
        self.assertEqual(config.llm.codex_command, "codex")

    def test_legacy_llm_fields_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            config_path.write_text(
                '[llm]\nbase_url = "http://localhost:11434/v1"\nmax_tokens = 180\n',
                encoding="utf-8",
            )
            config = load_config(config_path)

        self.assertEqual(config.llm.provider, "codex")
        self.assertFalse(hasattr(config.llm, "base_url"))


if __name__ == "__main__":
    unittest.main()
