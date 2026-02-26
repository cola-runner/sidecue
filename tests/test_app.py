from __future__ import annotations

import threading
import unittest
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sidecue.app import SidecueApp
from sidecue.config import load_config


class _AlwaysErrorSource:
    def __init__(self, message: str, exc_type: type = RuntimeError) -> None:
        self._message = message
        self._exc_type = exc_type
        self.calls = 0

    def next_utterance(self) -> str | None:
        self.calls += 1
        raise self._exc_type(self._message)


class _ListSource:
    def __init__(self, utterances: list[str | None]) -> None:
        self._utterances = list(utterances)

    def next_utterance(self) -> str | None:
        if not self._utterances:
            return None
        return self._utterances.pop(0)


class _FakeStopEvent:
    def __init__(self) -> None:
        self._is_set = False
        self.wait_calls: list[float | None] = []

    def is_set(self) -> bool:
        return self._is_set

    def set(self) -> None:
        self._is_set = True

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_calls.append(timeout)
        self._is_set = True
        return True


def _build_minimal_app(source: object, stop_event: object | None = None) -> tuple[SidecueApp, list[object]]:
    app = SidecueApp.__new__(SidecueApp)
    app._show_prompt = False
    app._config = SimpleNamespace(
        retrieval=SimpleNamespace(top_k=3, chunk_chars=700, overlap_chars=120),
        llm=SimpleNamespace(system_prompt="test system prompt"),
    )
    app._stop_event = stop_event or threading.Event()
    app._pending = Queue(maxsize=1)
    app._input_done = threading.Event()
    app._auto_close = threading.Event()
    app._asr_error = ""
    app._overlay = None
    app._transcript_source = source
    app._llm = SimpleNamespace(generate=lambda prompt: "ok")
    app._retriever = SimpleNamespace(search=lambda utterance, top_k: [])
    app._retriever_lock = threading.Lock()
    app._document_paths = []

    updates: list[object] = []
    app._publish = updates.append
    return app, updates


class AppAsrErrorHandlingTests(unittest.TestCase):
    def test_fatal_asr_error_stops_loop_immediately(self) -> None:
        source = _AlwaysErrorSource("Speech recognition access denied.", exc_type=PermissionError)
        app, updates = _build_minimal_app(source)

        app._run_loop()

        self.assertEqual(source.calls, 1)
        self.assertTrue(app._input_done.is_set())
        self.assertFalse(app._stop_event.is_set())
        self.assertEqual(len(updates), 1)
        self.assertIn("non-recoverable error", updates[0].suggestion)
        self.assertEqual(updates[0].phase, "input_error")

    def test_non_fatal_asr_error_uses_backoff_wait(self) -> None:
        source = _AlwaysErrorSource("Temporary audio device failure")
        fake_stop_event = _FakeStopEvent()
        app, updates = _build_minimal_app(source, stop_event=fake_stop_event)

        app._run_loop()

        self.assertEqual(source.calls, 1)
        self.assertEqual(fake_stop_event.wait_calls, [0.5])
        self.assertEqual(len(updates), 1)
        self.assertIn("Retrying in", updates[0].suggestion)


class AppPrivacyTests(unittest.TestCase):
    def test_transcript_and_generation_error_are_not_logged(self) -> None:
        transcript = "private transcript fixture"
        error_detail = "private model error fixture"
        app, updates = _build_minimal_app(_ListSource([transcript, None]))
        app._llm = MagicMock()
        app._llm.generate.side_effect = RuntimeError(error_detail)

        with self.assertLogs("sidecue.app", level="INFO") as captured:
            app._run_loop()
            app._generate_loop()

        logs = "\n".join(captured.output)
        self.assertNotIn(transcript, logs)
        self.assertNotIn(error_detail, logs)
        self.assertIn(f"chars={len(transcript)}", logs)
        self.assertIn("RuntimeError", logs)
        self.assertEqual(updates[-1].transcript, transcript)
        self.assertIn(error_detail, updates[-1].suggestion)

    def test_input_initialization_error_is_not_logged(self) -> None:
        error_detail = "private input error fixture"
        app, updates = _build_minimal_app(None)
        app._config.asr = SimpleNamespace(mode="mic")
        with patch("sidecue.app.create_transcript_source", side_effect=RuntimeError(error_detail)), \
                self.assertLogs("sidecue.app", level="WARNING") as captured:
            app._run_loop()
        self.assertNotIn(error_detail, "\n".join(captured.output))
        self.assertEqual(updates[-1].suggestion, error_detail)
        self.assertTrue(app._input_done.is_set())

    def test_ui_failure_does_not_fall_back_to_console_or_start_input(self) -> None:
        config = load_config("config.toml")
        with patch("sidecue.app.create_overlay", side_effect=RuntimeError("private UI error fixture")), \
                patch("sidecue.app.create_llm_client") as model, \
                patch("sidecue.app.create_transcript_source") as source, \
                self.assertRaises(RuntimeError) as error:
            SidecueApp(config)
        self.assertIn("--no-ui", str(error.exception))
        self.assertNotIn("private UI error fixture", str(error.exception))
        model.assert_not_called()
        source.assert_not_called()


class AppPromptDebugTests(unittest.TestCase):
    def test_show_prompt_includes_generation_prompt_in_update(self) -> None:
        app, updates = _build_minimal_app(_ListSource(["hello", None]))
        app._show_prompt = True

        app._run_loop()
        app._generate_loop()

        self.assertGreaterEqual(len(updates), 1)
        self.assertIn("[CURRENT INPUT]", updates[0].prompt)
        self.assertIn("hello", updates[0].prompt)
        self.assertEqual(updates[0].phase, "generating")
        self.assertEqual(updates[-1].phase, "ready")
        self.assertGreaterEqual(updates[-1].elapsed_seconds, 0)

    def test_generation_failure_has_separate_error_state(self) -> None:
        app, updates = _build_minimal_app(_ListSource(["hello", None]))
        app._llm = MagicMock()
        app._llm.generate.side_effect = TimeoutError("timeout")
        app._run_loop()
        app._generate_loop()
        self.assertEqual(updates[-1].phase, "error")
        self.assertIn("timeout", updates[-1].suggestion)

    def test_pending_transcripts_are_bounded_and_keep_latest(self) -> None:
        app, updates = _build_minimal_app(_ListSource(["first", "second", "third", None]))
        app._run_loop()
        self.assertEqual(app._pending.qsize(), 1)
        app._generate_loop()
        self.assertEqual(updates[-1].transcript, "third")

    def test_generation_does_not_block_capture(self) -> None:
        generating = threading.Event()
        captured = threading.Event()

        class Source:
            calls = 0
            def next_utterance(self):
                self.calls += 1
                if self.calls == 1:
                    return "first"
                if self.calls == 2:
                    if not generating.wait(2):
                        raise AssertionError("generation never started")
                    captured.set()
                    return "second"
                return None

        app, updates = _build_minimal_app(Source())
        def generate(prompt):
            generating.set()
            if not captured.wait(2):
                raise AssertionError("capture blocked by generation")
            return "ok"
        app._llm = SimpleNamespace(generate=generate)
        app.run()
        self.assertTrue(captured.is_set())
        self.assertEqual([u.transcript for u in updates if u.suggestion == "ok"], ["first", "second"])

    def test_stop_closes_source_and_llm(self) -> None:
        source = MagicMock()
        app, _ = _build_minimal_app(source)
        app._llm = MagicMock()
        app.stop()
        source.close.assert_called_once()
        app._llm.close.assert_called_once()


class AppDocumentManagementTests(unittest.TestCase):
    def test_add_documents_appends_new_paths(self) -> None:
        source = _AlwaysErrorSource("unused")
        app, _ = _build_minimal_app(source)
        app._document_paths = ["/existing/a.txt"]

        # Patch _rebuild_retriever to avoid actual file I/O
        rebuild_calls: list[bool] = []
        app._rebuild_retriever = lambda: rebuild_calls.append(True)

        app.add_documents(["/new/b.txt", "/existing/a.txt"])

        self.assertEqual(app._document_paths, ["/existing/a.txt", "/new/b.txt"])

    def test_add_documents_skips_all_duplicates(self) -> None:
        source = _AlwaysErrorSource("unused")
        app, _ = _build_minimal_app(source)
        app._document_paths = ["/a.txt"]
        app._rebuild_retriever = lambda: None

        app.add_documents(["/a.txt"])

        self.assertEqual(app._document_paths, ["/a.txt"])

    def test_remove_documents_filters_paths(self) -> None:
        source = _AlwaysErrorSource("unused")
        app, _ = _build_minimal_app(source)
        app._document_paths = ["/a.txt", "/b.txt", "/c.txt"]

        rebuild_calls: list[bool] = []
        app._rebuild_retriever = lambda: rebuild_calls.append(True)

        app.remove_documents(["/b.txt"])

        self.assertEqual(app._document_paths, ["/a.txt", "/c.txt"])


if __name__ == "__main__":
    unittest.main()
