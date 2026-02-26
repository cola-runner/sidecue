from __future__ import annotations

import logging
import math
import sys
import threading
import time
from dataclasses import dataclass, replace
from typing import Protocol

from .config import AsrConfig

logger = logging.getLogger(__name__)


class TranscriptSource(Protocol):
    def next_utterance(self) -> str | None:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class SpeechStatus:
    state: str = "Not started"
    partial: str = ""
    level_dbfs: float = -120.0
    peak_dbfs: float = -120.0
    buffers: int = 0
    detail: str = ""


class StdinTranscriptSource:
    def close(self) -> None:
        pass

    def next_utterance(self) -> str | None:
        # Use sys.stdin.readline() instead of input() to avoid Tkinter's
        # EventHook crash when called from a worker thread on macOS.
        sys.stdout.write("You> ")
        sys.stdout.flush()
        try:
            line = sys.stdin.readline()
        except EOFError:
            return None
        if not line:
            return None
        return line.strip()


class MacOSTranscriptSource:
    """Real-time speech recognition using macOS SFSpeechRecognizer."""

    def __init__(self, language: str = "en-US", on_device: bool = True) -> None:
        try:
            import objc  # noqa: F401
            from AVFoundation import AVAudioEngine, AVCaptureDevice, AVMediaTypeAudio
            from Foundation import NSDate, NSBundle, NSLocale, NSOperationQueue, NSRunLoop
            from Speech import (
                SFSpeechAudioBufferRecognitionRequest,
                SFSpeechRecognizer,
                SFSpeechRecognizerAuthorizationStatusAuthorized,
            )
        except ImportError as exc:
            raise RuntimeError(
                "macOS speech recognition requires PyObjC. "
                "Install: pip install pyobjc-framework-Speech pyobjc-framework-AVFoundation"
            ) from exc

        self._SFSpeechAudioBufferRecognitionRequest = SFSpeechAudioBufferRecognitionRequest
        _assert_macos_speech_usage_descriptions(NSBundle.mainBundle())

        def authorize(request, current, allowed, name):
            logger.info("%s authorization status: %s", name, current)
            if current == allowed:
                return
            if current != 0:
                raise PermissionError(f"{name} access denied. Allow Sidecue in System Settings > Privacy & Security.")
            result = []
            request(lambda value: result.append(value))
            deadline = time.monotonic() + 60
            while not result:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{name} authorization timed out. Check Sidecue in System Settings > Privacy & Security.")
                NSRunLoop.currentRunLoop().runUntilDate_(
                    NSDate.dateWithTimeIntervalSinceNow_(0.05)
                )
                time.sleep(0.02)
            if result[0] != allowed:
                raise PermissionError(f"{name} access denied. Allow Sidecue in System Settings > Privacy & Security.")
            logger.info("%s authorized", name)

        def request_microphone(callback):
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AVMediaTypeAudio, lambda granted: callback(3 if granted else 2)
            )

        authorize(
            SFSpeechRecognizer.requestAuthorization_,
            SFSpeechRecognizer.authorizationStatus(),
            SFSpeechRecognizerAuthorizationStatusAuthorized,
            "Speech recognition",
        )
        authorize(
            request_microphone,
            AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio),
            3,
            "Microphone",
        )
        locale = NSLocale.alloc().initWithLocaleIdentifier_(language)
        self._recognizer = SFSpeechRecognizer.alloc().initWithLocale_(locale)
        if not self._recognizer or not self._recognizer.isAvailable():
            raise RuntimeError(f"SFSpeechRecognizer unavailable (locale={language})")
        self._on_device = on_device
        if on_device and not self._recognizer.supportsOnDeviceRecognition():
            raise RuntimeError(f"On-device recognition is unavailable for {language}. Check system dictation languages. Setting asr.on_device=false explicitly allows Apple online recognition.")
        # A worker waiting for speech cannot service the main operation queue.
        self._callback_queue = NSOperationQueue.alloc().init()
        self._callback_queue.setMaxConcurrentOperationCount_(1)
        self._callback_queue.setName_("sidecue-speech")
        self._recognizer.setQueue_(self._callback_queue)
        logger.info(
            "Speech: locale=%s, on_device=%s, callback_queue=background", language, on_device
        )
        self._engine = AVAudioEngine.alloc().init()
        self._input_node = self._engine.inputNode()
        self._record_format = self._input_node.outputFormatForBus_(0)
        if self._record_format.sampleRate() <= 0 or self._record_format.channelCount() <= 0:
            raise RuntimeError("The default microphone has no valid audio format. Check the system input device.")
        logger.info(
            "Default input: sample_rate=%s, channels=%s",
            self._record_format.sampleRate(),
            self._record_format.channelCount(),
        )
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._changed = threading.Event()
        self._status = SpeechStatus()
        self._request = None
        self._task = None
        self._started = False
        self._tap_installed = False
        self._silence_seconds = 2.5
        self._last_text = ""
        self._last_result_at = 0.0
        self._final = False
        self._error = None
        self._generation = 0
        self._signal_seconds = 0.0
        self._tap_handler = None

    @property
    def status(self) -> SpeechStatus:
        with self._lock:
            return self._status

    def _start_recognition(self) -> None:
        if self._closed.is_set():
            return
        request = self._SFSpeechAudioBufferRecognitionRequest.alloc().init()
        request.setShouldReportPartialResults_(True)
        request.setRequiresOnDeviceRecognition_(self._on_device)
        self._request = request
        self._last_text = ""
        self._last_result_at = time.monotonic()
        self._final = False
        self._error = None
        self._signal_seconds = 0.0
        self._generation += 1
        generation = self._generation
        self._status = replace(self._status, state="Listening", detail="", partial="")

        def result_handler(result: object, error: object) -> None:
            self._receive_result(generation, result, error)

        self._task = self._recognizer.recognitionTaskWithRequest_resultHandler_(
            request, result_handler
        )
        if not self._started:
            self._tap_handler = self._receive_audio
            self._input_node.installTapOnBus_bufferSize_format_block_(
                0, 1024, self._record_format, self._tap_handler
            )
            self._tap_installed = True
            success, error = self._engine.startAndReturnError_(None)
            if not success:
                self._input_node.removeTapOnBus_(0)
                self._tap_installed = False
                raise RuntimeError(f"AVAudioEngine failed to start: {error}")
            self._started = True
            logger.info("AVAudioEngine started")

    def _receive_audio(self, buffer: object, when: object) -> None:
        try:
            frames = buffer.frameLength()
            channels = buffer.floatChannelData()
            samples = channels[0] if channels and frames else None
            indices = range(0, frames, max(1, frames // 256))
            rms = 0.0
            if samples is not None:
                rms = math.sqrt(sum(float(samples[i]) ** 2 for i in indices) / len(indices))
            dbfs = max(-120.0, 20 * math.log10(max(rms, 1e-6)))
            with self._lock:
                if self._closed.is_set():
                    return
                self._status = replace(
                    self._status,
                    level_dbfs=dbfs,
                    peak_dbfs=max(dbfs, self._status.peak_dbfs),
                    buffers=self._status.buffers + 1,
                )
                if dbfs > -45:
                    self._signal_seconds += frames / self._record_format.sampleRate()
                if self._request is not None:
                    self._request.appendAudioPCMBuffer_(buffer)
        except Exception as exc:
            with self._lock:
                self._error = RuntimeError(f"Cannot read microphone audio: {exc}")
            self._changed.set()

    def _receive_result(self, generation: int, result: object, error: object) -> None:
        with self._lock:
            if generation != self._generation or self._closed.is_set():
                return
            if result:
                self._last_text = str(result.bestTranscription().formattedString())
                self._last_result_at = time.monotonic()
                self._final = bool(result.isFinal())
                self._status = replace(self._status, partial=self._last_text, detail="")
                logger.info("ASR %s: chars=%d", "final" if self._final else "partial", len(self._last_text))
            if error and not self._final:
                self._error = RuntimeError(
                    f"{error.domain()} ({error.code()}): {error.localizedDescription()}"
                )
                logger.warning("ASR error: code=%s", error.code())
        self._changed.set()

    def _end_recognition(self) -> None:
        with self._lock:
            self._generation += 1
            request, task = self._request, self._task
            self._request = self._task = None
        if request is not None:
            request.endAudio()
        if task is not None:
            task.cancel()

    def next_utterance(self, timeout: float | None = None) -> str | None:
        started_at = time.monotonic()
        try:
            with self._lock:
                self._start_recognition()
            session_started = time.monotonic()
            while not self._closed.is_set():
                self._changed.wait(0.1)
                self._changed.clear()
                now = time.monotonic()
                with self._lock:
                    if self._error is not None:
                        raise self._error
                    segment_finished = (
                        self._final
                        or now - self._last_result_at >= self._silence_seconds
                        or now - session_started >= 45
                    )
                    if self._last_text and segment_finished:
                        return self._last_text
                    if timeout is not None and now - started_at >= timeout:
                        raise TimeoutError(
                            f"No transcript within {timeout:g}s; buffers={self._status.buffers}, "
                            f"peak={self._status.peak_dbfs:.1f} dBFS. "
                            "Check the input device, volume, and system dictation language."
                        )
                    if not self._last_text and self._signal_seconds >= 2 and now - session_started >= 20:
                        # Audio energy also includes background noise, not only speech.
                        self._status = replace(
                            self._status, detail="Audio received; waiting for recognizable speech"
                        )
                if now - session_started >= 45:
                    self._end_recognition()
                    with self._lock:
                        self._start_recognition()
                    session_started = time.monotonic()
            return None
        except Exception as exc:
            with self._lock:
                self._status = replace(self._status, state="Recognition failed", detail=str(exc))
            raise
        finally:
            self._end_recognition()

    def check_ready(self, timeout: float = 15.0) -> str:
        """Success requires real recognition, not just a started audio engine."""
        try:
            text = self.next_utterance(timeout=timeout)
            if not text:
                raise RuntimeError("Recognition stopped without a transcript.")
            return text
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            self._closed.set()
            self._status = replace(self._status, state="Stopped", level_dbfs=-120.0)
        self._changed.set()
        self._end_recognition()
        if self._tap_installed:
            self._input_node.removeTapOnBus_(0)
            self._tap_installed = False
        if self._started:
            self._engine.stop()
            self._started = False


def create_transcript_source(config: AsrConfig) -> TranscriptSource:
    mode = config.mode.lower().strip()
    if mode == "stdin":
        return StdinTranscriptSource()
    if mode == "mic":
        return MacOSTranscriptSource(language=config.language, on_device=config.on_device)
    raise ValueError(f"Unsupported ASR mode: {config.mode}")


def _assert_macos_speech_usage_descriptions(bundle: object) -> None:
    missing: list[str] = []
    for key in ("NSSpeechRecognitionUsageDescription", "NSMicrophoneUsageDescription"):
        value = bundle.objectForInfoDictionaryKey_(key)
        if not value:
            missing.append(key)
    if not missing:
        return
    joined = ", ".join(missing)
    raise RuntimeError(
        "This process has no macOS speech/microphone usage declarations; mic mode cannot start safely. "
        f"Missing Info.plist keys: {joined}. "
        "Launch the .app bundle with these declarations, or use stdin mode for testing."
    )
