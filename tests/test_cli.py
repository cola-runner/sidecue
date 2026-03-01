from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sidecue.cli import _main


class MicrophoneCheckPrivacyTests(unittest.TestCase):
    def test_check_does_not_print_recognized_text(self):
        transcript = "private microphone fixture"
        source = MagicMock()
        source.check_ready.return_value = transcript
        source.status = SimpleNamespace(buffers=12, peak_dbfs=-20.0)
        output = io.StringIO()
        with patch("sys.argv", ["sidecue", "--check-mic", "--check-seconds", "1"]), \
                patch("sidecue.cli.MacOSTranscriptSource", return_value=source), \
                redirect_stdout(output):
            _main()
        self.assertNotIn(transcript, output.getvalue())
        self.assertIn(f"MIC_CHECK PASS: recognized_chars={len(transcript)}", output.getvalue())
        source.check_ready.assert_called_once_with(timeout=1)


class PreviewCliTests(unittest.TestCase):
    def test_preview_never_initializes_app_microphone_or_model(self):
        with patch("sys.argv", ["sidecue", "--preview-ui", "--run-seconds", "5"]), \
                patch("sidecue.cli.SidecueApp") as app, \
                patch("sidecue.cli.MacOSTranscriptSource") as speech, \
                patch("sidecue.ui_preview.run_ui_preview") as preview:
            _main()
        app.assert_not_called()
        speech.assert_not_called()
        preview.assert_called_once()
        self.assertEqual(preview.call_args.kwargs["duration_seconds"], 5)

    def test_preview_rejects_microphone_check(self):
        with patch("sys.argv", ["sidecue", "--preview-ui", "--check-mic"]), \
                patch("sidecue.cli.MacOSTranscriptSource") as speech:
            with self.assertRaises(SystemExit) as error:
                _main()
        self.assertEqual(error.exception.code, 2)
        speech.assert_not_called()
