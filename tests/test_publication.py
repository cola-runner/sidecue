from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import struct
import subprocess
import unittest

from sidecue.config import load_config
from sidecue.overlay import cue_lines
from scripts.build_macos_app import APP_IDENTIFIER, APP_NAME


ROOT = Path(__file__).resolve().parents[1]


class _Images(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.paths.append(dict(attrs)["src"])


class PublicationTests(unittest.TestCase):
    def test_project_license_and_readme_agree(self):
        license_text = (ROOT / "LICENSE").read_text()
        self.assertTrue(license_text.startswith("MIT License\n"))
        self.assertIn("Copyright (c) 2026 Ezreal", license_text)
        self.assertIn("[MIT License](LICENSE)", (ROOT / "README.md").read_text())

    def test_public_text_is_english(self):
        candidates = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
        ).decode().split("\0")
        ideographs = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U000323af]")
        text_suffixes = {".py", ".md", ".toml", ".m", ".sh", ".mjs", ".txt"}
        for name in set(candidates) - {""}:
            path = ROOT / name
            if path.is_file() and not path.is_symlink() and path.suffix in text_suffixes:
                with self.subTest(file=name):
                    self.assertIsNone(ideographs.search(path.read_text()), name)

    def test_brand_and_default_language_match(self):
        config = load_config(ROOT / "config.toml")
        self.assertEqual(config.ui.title, "Sidecue")
        self.assertEqual(APP_NAME, "Sidecue")
        self.assertEqual(APP_IDENTIFIER, "local.sidecue")
        self.assertEqual(config.asr.language, "en-US")
        self.assertIn("Write in English", config.llm.system_prompt)

    def test_readme_includes_a_local_preview_image(self):
        parser = _Images()
        parser.feed((ROOT / "README.md").read_text())
        self.assertIn("docs/images/sidecue-preview.png", parser.paths)
        data = (ROOT / "docs/images/sidecue-preview.png").read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        self.assertGreaterEqual(width, 440)
        self.assertGreaterEqual(height, 340)

    def test_legacy_model_labels_remain_readable(self):
        value = "1. \u63d0\u793a\uff1a Legacy cue\n\u4f9d\u636e\uff1a Legacy evidence"
        self.assertEqual(cue_lines(value), [
            ("cue", "Legacy cue"), ("evidence", "\u4f9d\u636e\uff1a Legacy evidence"),
        ])


if __name__ == "__main__":
    unittest.main()
