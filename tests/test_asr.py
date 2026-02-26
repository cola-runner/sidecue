from __future__ import annotations

import unittest
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sidecue.asr import MacOSTranscriptSource, _assert_macos_speech_usage_descriptions


class _FakeBundle:
    def __init__(self, values: dict[str, str | None]) -> None:
        self._values = values

    def objectForInfoDictionaryKey_(self, key: str) -> str | None:
        return self._values.get(key)


class MacOSSpeechPreflightTests(unittest.TestCase):
    def test_usage_descriptions_pass_when_present(self) -> None:
        bundle = _FakeBundle(
            {
                "NSSpeechRecognitionUsageDescription": "speech",
                "NSMicrophoneUsageDescription": "mic",
            }
        )

        _assert_macos_speech_usage_descriptions(bundle)

    def test_usage_descriptions_raise_when_missing(self) -> None:
        bundle = _FakeBundle({"NSSpeechRecognitionUsageDescription": "speech"})

        with self.assertRaisesRegex(RuntimeError, "NSMicrophoneUsageDescription"):
            _assert_macos_speech_usage_descriptions(bundle)


class SpeechLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.recognizer = MagicMock()
        self.engine = MagicMock()
        self.engine.startAndReturnError_.return_value = (True, None)
        self.format = self.engine.inputNode.return_value.outputFormatForBus_.return_value
        self.format.sampleRate.return_value = 16000
        self.format.channelCount.return_value = 1
        self.request = MagicMock()
        self.speech = MagicMock()
        self.speech.SFSpeechRecognizer.authorizationStatus.return_value = 3
        self.speech.SFSpeechRecognizerAuthorizationStatusAuthorized = 3
        self.speech.SFSpeechRecognizer.alloc.return_value.initWithLocale_.return_value = self.recognizer
        self.speech.SFSpeechAudioBufferRecognitionRequest.alloc.return_value.init.return_value = self.request
        foundation = MagicMock()
        av = MagicMock()
        av.AVAudioEngine.alloc.return_value.init.return_value = self.engine
        av.AVCaptureDevice.authorizationStatusForMediaType_.return_value = 3
        self.device_name = "Private Fixture's Headset"
        av.AVCaptureDevice.defaultDeviceWithMediaType_.return_value.localizedName.return_value = self.device_name
        with patch.dict("sys.modules", {"objc": MagicMock(), "Foundation": foundation, "AVFoundation": av, "Speech": self.speech}), \
                self.assertLogs("sidecue.asr", level="INFO") as captured:
            self.source = MacOSTranscriptSource()
        self.initialization_logs = "\n".join(captured.output)
        self.callback_queue = foundation.NSOperationQueue.alloc.return_value.init.return_value
        self.addCleanup(self.source.close)

    def result(self, text="hello", final=True):
        result = MagicMock()
        result.bestTranscription.return_value.formattedString.return_value = text
        result.isFinal.return_value = final
        return result

    def callback_with(self, result=None, error=None):
        def start(request, callback):
            callback(result, error)
            return MagicMock()
        self.recognizer.recognitionTaskWithRequest_resultHandler_.side_effect = start

    def test_callback_queue_is_serial_and_not_main(self):
        self.recognizer.setQueue_.assert_called_once_with(self.callback_queue)
        self.callback_queue.setMaxConcurrentOperationCount_.assert_called_once_with(1)
        self.speech.SFSpeechRecognizer.requestAuthorization_.assert_not_called()

    def test_device_display_name_is_not_logged(self):
        self.assertNotIn(self.device_name, self.initialization_logs)
        self.assertIn("sample_rate=16000", self.initialization_logs)

    def test_native_result_is_delivered_and_task_is_released(self):
        self.callback_with(result=self.result())
        self.assertEqual(self.source.next_utterance(timeout=0.2), "hello")
        self.request.setRequiresOnDeviceRecognition_.assert_called_once_with(True)
        self.request.endAudio.assert_called_once()
        self.assertIsNone(self.source._task)

    def test_transcript_logs_only_character_count(self):
        transcript = "private speech fixture"
        self.callback_with(result=self.result(transcript))
        with self.assertLogs("sidecue.asr", level="INFO") as captured:
            self.assertEqual(self.source.next_utterance(timeout=0.2), transcript)
        logs = "\n".join(captured.output)
        self.assertNotIn(transcript, logs)
        self.assertIn(f"chars={len(transcript)}", logs)

    def test_error_log_omits_native_error_details(self):
        detail = "private native error fixture"
        error = SimpleNamespace(domain=lambda: "Speech", code=lambda: 1101, localizedDescription=lambda: detail)
        self.callback_with(error=error)
        with self.assertLogs("sidecue.asr", level="WARNING") as captured, \
                self.assertRaisesRegex(RuntimeError, detail):
            self.source.next_utterance(timeout=0.2)
        logs = "\n".join(captured.output)
        self.assertNotIn(detail, logs)
        self.assertIn("code=1101", logs)

    def test_native_error_reaches_caller(self):
        error = SimpleNamespace(domain=lambda: "Speech", code=lambda: 1101, localizedDescription=lambda: "service unavailable")
        self.callback_with(error=error)
        with self.assertRaisesRegex(RuntimeError, "1101.*service unavailable"):
            self.source.next_utterance(timeout=0.2)
        self.assertEqual(self.source.status.state, "Recognition failed")

    def test_partial_is_committed_after_silence_once(self):
        self.source._silence_seconds = 0
        self.callback_with(result=self.result(final=False))
        self.assertEqual(self.source.next_utterance(timeout=0.2), "hello")
        generation = self.source._generation - 1
        self.source._receive_result(generation, self.result("late stale result"), None)
        self.assertEqual(self.source.status.partial, "hello")

    def test_timeout_without_results_is_not_a_success(self):
        with self.assertRaisesRegex(TimeoutError, "No transcript"):
            self.source.check_ready(timeout=0.01)
        self.assertTrue(self.source._closed.is_set())

    def test_background_noise_does_not_abort_live_recognition(self):
        def start(request, callback):
            self.source._signal_seconds = 3
            return MagicMock()
        self.recognizer.recognitionTaskWithRequest_resultHandler_.side_effect = start
        waits = 0
        def wait(timeout):
            nonlocal waits
            waits += 1
            if waits == 2:
                self.assertEqual(self.source.status.state, "Listening")
                self.source._receive_result(self.source._generation, self.result(), None)
        with patch.object(self.source._changed, "wait", side_effect=wait):
            with patch("sidecue.asr.time.monotonic", side_effect=[0, 0, 0, 21, 21, 21]):
                self.assertEqual(self.source.next_utterance(), "hello")
        self.assertEqual(waits, 2)
        self.assertEqual(self.source.status.detail, "")

    def test_close_is_idempotent(self):
        self.source._start_recognition()
        self.source.close()
        self.source.close()
        self.engine.inputNode.return_value.removeTapOnBus_.assert_called_once_with(0)
        self.engine.stop.assert_called_once()
        self.request.endAudio.assert_called_once()

    def test_engine_start_failure_removes_tap_before_retry(self):
        self.engine.startAndReturnError_.return_value = (False, "device unavailable")
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "device unavailable"):
                self.source.next_utterance(timeout=0.1)
            self.assertFalse(self.source._tap_installed)
        self.assertEqual(self.engine.inputNode.return_value.removeTapOnBus_.call_count, 2)

    def test_close_wakes_waiter(self):
        entered = threading.Event()
        self.recognizer.recognitionTaskWithRequest_resultHandler_.side_effect = lambda *args: entered.set() or MagicMock()
        results = []
        thread = threading.Thread(target=lambda: results.append(self.source.next_utterance()), daemon=True)
        thread.start()
        self.assertTrue(entered.wait(1))
        self.source.close()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [None])

    def test_audio_meter_measures_samples_not_buffer_count(self):
        buffer = SimpleNamespace(frameLength=lambda: 1024, floatChannelData=lambda: [[0.1] * 1024])
        self.source._receive_audio(buffer, None)
        self.assertEqual(self.source.status.buffers, 1)
        self.assertAlmostEqual(self.source.status.level_dbfs, -20)
        self.source._receive_audio(SimpleNamespace(frameLength=lambda: 1024, floatChannelData=lambda: [[0.0] * 1024]), None)
        self.assertEqual(self.source.status.level_dbfs, -120)
        self.assertAlmostEqual(self.source.status.peak_dbfs, -20)


if __name__ == "__main__":
    unittest.main()
