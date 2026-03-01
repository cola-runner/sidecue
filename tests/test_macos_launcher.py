"""Opt-in tests of the compiled .app, not just its Python entry point.

Close the app before running:
RUN_GUI_TESTS=1 RUN_BUNDLE_TESTS=1 python -m unittest discover -s tests -q
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(
    sys.platform == "darwin" and os.environ.get("RUN_BUNDLE_TESTS") == "1",
    "Set RUN_BUNDLE_TESTS=1 to build and exercise the native launcher",
)
class MacOSLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        # Always rebuild: testing a stale executable can conceal launcher bugs.
        result = subprocess.run(
            [sys.executable, "scripts/build_macos_app.py"], cwd=cls.root,
            capture_output=True, text=True, timeout=90,
        )
        if result.returncode:
            raise RuntimeError(f"Cannot build test app: {result.stderr}")
        cls.executable = cls.root / "build/Sidecue.app/Contents/MacOS/Sidecue"
        cls.log_path = Path.home() / "Library/Logs/Sidecue/app.log"

    def run_app(self, *args, expected=0):
        # Read only this run's diagnostics; existing logs may contain private input.
        offset = self.log_path.stat().st_size if self.log_path.exists() else 0
        result = subprocess.run(
            [str(self.executable), *args], cwd=self.root, input="",
            capture_output=True, text=True, timeout=15,
        )
        with self.log_path.open("rb") as log:
            log.seek(offset)
            diagnostic = log.read().decode("utf-8", errors="replace")
        self.assertEqual(result.returncode, expected, diagnostic or result.stderr)
        for failure in ("Fatal Python error", "Segmentation fault", "Traceback (most recent call last)",
                        "ObjCPointerWarning", "autoreleased with no pool"):
            self.assertNotIn(failure, diagnostic)
        return diagnostic

    def test_repeated_preview_start_and_exit(self):
        # The old autorelease-pool scope crashed here after Python had finalized.
        for iteration in range(10):
            with self.subTest(iteration=iteration):
                diagnostic = self.run_app("--preview-ui", "--run-seconds", "0.3")
                self.assertIn("--- native exit status=0 ---", diagnostic)

    def test_mock_generation_and_native_window_shutdown(self):
        diagnostic = self.run_app(
            "--asr-mode", "stdin", "--llm-provider", "mock",
            "--text", "Native launcher regression test", "--run-seconds", "0.5",
        )
        self.assertIn("--- native exit status=0 ---", diagnostic)

    def test_mock_console_shutdown(self):
        self.run_app("--asr-mode", "stdin", "--llm-provider", "mock", "--no-ui",
                     "--text", "Console regression test", "--run-seconds", "0.3")

    def test_help_and_invalid_arguments_preserve_exit_codes(self):
        self.run_app("--help")
        self.run_app("--preview-ui", "--run-seconds", "0", expected=2)
        self.run_app("--preview-ui", "--no-ui", expected=2)


if __name__ == "__main__":
    unittest.main()
