import tempfile
import unittest
from pathlib import Path

from sidecue.runtime import app_lock


class AppLockTests(unittest.TestCase):
    def test_prevents_duplicate_run_and_releases_on_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.lock"
            with self.assertRaisesRegex(ValueError, "test"):
                with app_lock(path):
                    with self.assertRaisesRegex(RuntimeError, "already running"):
                        with app_lock(path):
                            self.fail("second lock must not succeed")
                    raise ValueError("test")
            with app_lock(path):
                pass

    def test_plain_cli_can_run_without_bundle_lock(self):
        with app_lock(None):
            pass
