from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sidecue.parser import load_documents
from scripts.build_macos_app import copy_public_resources
from scripts.check_public_tree import main, private_path, scan_content, scan_history, scan_worktree


class PublicTreeTests(unittest.TestCase):
    def test_secret_rules_report_positions_without_values(self):
        token = "ghp_" + "a" * 36
        home = "/".join(["", "Users", "fixture", "project"])
        findings = scan_content("sample.txt", f"{token}\n{home}".encode())
        self.assertEqual([(item.line, item.rule) for item in findings],
                         [(1, "github-token"), (2, "private-home-path")])
        with patch("sys.argv", ["check_public_tree.py"]), \
                patch("scripts.check_public_tree.scan_worktree", return_value=(findings, 1)), \
                redirect_stdout(io.StringIO()) as output:
            status = main()
        self.assertEqual(status, 1)
        self.assertIn("sample.txt:1: github-token", output.getvalue())
        self.assertNotIn(token, output.getvalue())
        self.assertNotIn(home, output.getvalue())

    def test_file_categories_and_public_examples(self):
        for path in [".env", ".env.production", "config.local.toml", "knowledge/private.txt",
                     "knowledge/subdir/private.pdf", "logs/app.log", "recordings/audio.wav",
                     ".codex/auth.json", "build/Sidecue.app/Contents/Info.plist"]:
            with self.subTest(path=path):
                self.assertTrue(private_path(path))
        for path in [".env.example", "config.toml", "knowledge/sample_notes.txt", "README.md"]:
            with self.subTest(path=path):
                self.assertFalse(private_path(path))

    def test_email_review_ignores_only_placeholder_and_anonymous_domains(self):
        address = "fixture" + "@" + "mail.invalid-domain.com"
        data = f"{address}\nfixture@example.com\nfixture@users.noreply.github.com".encode()
        findings = scan_content("notes.txt", data)
        self.assertEqual([(item.line, item.rule) for item in findings], [(1, "email-review")])

    def test_repository_ignore_rules(self):
        root = Path(__file__).resolve().parents[1]
        for path in [".env", "config.local.toml", "knowledge/private.txt", "knowledge/subdir/private.md",
                     "logs/app.log", "recordings/audio.wav", ".codex/auth.json", "build/output"]:
            with self.subTest(path=path):
                result = subprocess.run(["git", "-C", str(root), "check-ignore", "--no-index", "-q", path])
                self.assertEqual(result.returncode, 0)
        for path in ["config.toml", "knowledge/sample_notes.txt", ".env.example"]:
            with self.subTest(path=path):
                result = subprocess.run(["git", "-C", str(root), "check-ignore", "--no-index", "-q", path])
                self.assertEqual(result.returncode, 1)

    def test_tracked_ignored_files_and_deleted_history_are_still_scanned(self):
        token = "sk-" + "x" * 32
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

            git("init", "-q")
            git("config", "user.name", "Test Fixture")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "commit.gpgsign", "false")
            git("config", "core.hooksPath", str(root / "empty-hooks"))
            (root / ".env").write_text(token)
            git("add", ".env")
            git("commit", "-qm", "Add synthetic test fixture")
            (root / ".gitignore").write_text(".env\n")
            findings, count = scan_worktree(root)
            self.assertEqual(count, 2)
            self.assertEqual({item.rule for item in findings}, {"private-file", "openai-key"})

            git("rm", "-q", ".env")
            git("add", ".gitignore")
            git("commit", "-qm", "Remove synthetic test fixture")
            self.assertEqual(scan_worktree(root)[0], [])
            historical, commits, emails = scan_history(root)
            self.assertEqual(commits, 2)
            self.assertEqual(emails, 0)
            self.assertEqual({item.rule for item in historical}, {"private-file", "openai-key"})
            self.assertNotIn(token, repr(historical))


class BuildResourcePrivacyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "repo"
        self.resources = Path(directory.name) / "resources"
        for name, content in {
            "sidecue/__init__.py": "",
            "sidecue/.env": "private fixture",
            "sidecue/assets/copy.png": "image fixture",
            "sidecue/assets/LUCIDE-LICENSE": "license fixture",
            "sidecue/assets/private.txt": "private fixture",
            "config.toml": "public fixture",
            "LICENSE": "public license fixture",
            "config.local.toml": "private fixture",
            "knowledge/sample_notes.txt": "public fixture",
            "knowledge/private.txt": "private fixture",
            "knowledge/subdir/private.txt": "private fixture",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def test_build_includes_only_public_resource_types_and_sample(self):
        (self.root / "sidecue" / "secret.py").symlink_to(self.root / "config.local.toml")
        (self.root / "sidecue" / "assets" / "secret.png").symlink_to(self.root / "config.local.toml")
        copy_public_resources(self.root, self.resources)
        files = {path.relative_to(self.resources).as_posix()
                 for path in self.resources.rglob("*") if path.is_file()}
        self.assertEqual(files, {"sidecue/__init__.py", "sidecue/assets/copy.png",
                                 "sidecue/assets/LUCIDE-LICENSE", "config.toml", "LICENSE", "knowledge/sample_notes.txt"})

    def test_build_rejects_public_files_pointing_to_private_file(self):
        for name in ["knowledge/sample_notes.txt", "config.toml", "LICENSE"]:
            with self.subTest(path=name):
                path = self.root / name
                path.unlink()
                path.symlink_to(self.root / "config.local.toml")
                with self.assertRaises(ValueError):
                    copy_public_resources(self.root, self.resources)
                path.unlink()
                path.write_text("public fixture")


class DocumentLogPrivacyTests(unittest.TestCase):
    def test_parser_error_omits_filename_and_details(self):
        path = Path("private-meeting-fixture.txt")
        detail = "private parser error fixture"
        with patch("sidecue.parser._discover_files", return_value=[path]), \
                patch("sidecue.parser._read_file", side_effect=ValueError(detail)), \
                self.assertLogs("sidecue.parser", level="WARNING") as captured:
            self.assertEqual(load_documents([str(path)]), [])
        logs = "\n".join(captured.output)
        self.assertNotIn(path.name, logs)
        self.assertNotIn(detail, logs)
        self.assertIn("ValueError", logs)


if __name__ == "__main__":
    unittest.main()
