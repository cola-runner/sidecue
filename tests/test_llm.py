from __future__ import annotations

import unittest
import subprocess
import sys
import threading
import time
from unittest.mock import patch, MagicMock

from sidecue.llm import (
    AppleFmClient,
    CodexCliClient,
    MockLlmClient,
    create_llm_client,
)
from sidecue.config import LlmConfig


class MockLlmClientTests(unittest.TestCase):
    def test_generate_returns_string(self) -> None:
        client = MockLlmClient()
        result = client.generate("test prompt")
        self.assertIsInstance(result, str)
        self.assertIn("[mock preview]", result)


class CodexCliClientTests(unittest.TestCase):
    @patch("sidecue.llm._build_codex_args", return_value=[sys.executable, "-c", "import time; time.sleep(20)"])
    @patch("sidecue.llm._resolve_codex_command", return_value=sys.executable)
    def test_close_terminates_a_real_owned_child(self, resolve, args):
        client = CodexCliClient("spark", "low", 25)
        errors = []
        def generate():
            try:
                client.generate("test")
            except RuntimeError as exc:
                errors.append(exc)
        worker = threading.Thread(target=generate, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 2
            while client._process is None and time.monotonic() < deadline:
                time.sleep(0.01)
            process = client._process
            self.assertIsNotNone(process)
            client.close()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertIsNotNone(process.poll())
            self.assertTrue(errors)
        finally:
            client.close()
            worker.join(2)

    @patch("sidecue.llm._resolve_codex_command", return_value="/usr/bin/codex")
    @patch("sidecue.llm.subprocess.Popen")
    def test_generate_reads_output_last_message(
        self, mock_run: MagicMock, _: MagicMock
    ) -> None:
        def fake_run(args: list[str], **kwargs: object) -> MagicMock:
            output_path = args[args.index("--output-last-message") + 1]
            with open(output_path, "w", encoding="utf-8") as fh:
                fh.write("  1. Prompt: Confirm milestones.  ")
            process = MagicMock(returncode=0)
            process.communicate.return_value = ("", "")
            return process

        mock_run.side_effect = fake_run

        client = CodexCliClient(
            model="gpt-5.3-codex-spark",
            reasoning_effort="low",
            timeout_seconds=10.0,
        )
        result = client.generate("hello")

        self.assertEqual(result, "1. Prompt: Confirm milestones.")
        args = mock_run.call_args[0][0]
        self.assertIn("gpt-5.3-codex-spark", args)
        self.assertIn('model_reasoning_effort="low"', args)
        self.assertIn("--ephemeral", args)
        self.assertIn("--ignore-user-config", args)
        self.assertEqual(args[-1], "-")
        self.assertIn('forced_login_method="chatgpt"', args)
        self.assertTrue(mock_run.call_args.kwargs["start_new_session"])

    @patch("sidecue.llm._resolve_codex_command", return_value="/usr/bin/codex")
    @patch("sidecue.llm.subprocess.Popen")
    def test_stdout_diagnostics_are_not_treated_as_a_reply(self, popen, resolve):
        process = popen.return_value
        process.returncode = 0
        process.communicate.return_value = ("diagnostic output", "")
        with self.assertRaisesRegex(RuntimeError, "returned no content"):
            CodexCliClient("spark", "low", 1).generate("test")

    @patch("sidecue.llm._resolve_codex_command", return_value="/usr/bin/codex")
    @patch("sidecue.llm.subprocess.Popen")
    @patch("sidecue.llm.CodexCliClient._terminate")
    def test_timeout_terminates_and_reaps_process(self, terminate, popen, resolve):
        process = popen.return_value
        process.communicate.side_effect = [subprocess.TimeoutExpired("codex", 1), ("", "")]
        client = CodexCliClient("spark", "low", 1)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            client.generate("test")
        terminate.assert_called_once_with(process)
        self.assertIsNone(client._process)

    @patch("sidecue.llm._resolve_codex_command", return_value="/usr/bin/codex")
    @patch("sidecue.llm.subprocess.Popen")
    def test_closed_client_cannot_start_a_process(self, popen, resolve):
        client = CodexCliClient("spark", "low", 1)
        client.close()
        with self.assertRaisesRegex(RuntimeError, "stopped"):
            client.generate("test")
        popen.assert_not_called()


class CreateLlmClientTests(unittest.TestCase):
    def _make_config(self, **overrides: object) -> LlmConfig:
        values = {
            "provider": "codex",
            "model": "gpt-5.3-codex-spark",
            "reasoning_effort": "low",
            "timeout_seconds": 30.0,
            "codex_command": "codex",
            "system_prompt": "test",
        }
        values.update(overrides)
        return LlmConfig(**values)

    def test_mock_mode(self) -> None:
        config = self._make_config(provider="mock")
        client = create_llm_client(config)
        self.assertIsInstance(client, MockLlmClient)

    def test_codex_mode(self) -> None:
        config = self._make_config(provider="codex")
        client = create_llm_client(config)
        self.assertIsInstance(client, CodexCliClient)

    def test_apple_fm_mode(self) -> None:
        with patch("sidecue.llm.AppleFmClient") as mock_cls:
            mock_client = MagicMock(spec=AppleFmClient)
            mock_cls.return_value = mock_client
            config = self._make_config(provider="apple_fm", system_prompt="abc")

            client = create_llm_client(config)

        self.assertIs(client, mock_client)
        mock_cls.assert_called_once_with(instructions="abc")

    def test_unknown_provider_raises(self) -> None:
        config = self._make_config(provider="unknown")
        with self.assertRaises(ValueError):
            create_llm_client(config)


if __name__ == "__main__":
    unittest.main()
