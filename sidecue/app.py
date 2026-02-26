from __future__ import annotations

import logging
import threading
import time
from queue import Empty, Full, Queue

from .asr import SpeechStatus, create_transcript_source
from .config import AppConfig
from .llm import create_llm_client
from .overlay import OverlayUpdate, create_overlay
from .parser import load_documents
from .prompting import build_generation_prompt
from .retriever import KeywordRetriever, build_chunks

logger = logging.getLogger(__name__)


def _is_fatal_asr_error(exc: Exception) -> bool:
    return isinstance(exc, (ImportError, ModuleNotFoundError, ValueError, PermissionError))


def _asr_retry_delay_seconds(error_count: int) -> float:
    return min(8.0, 0.5 * (2 ** max(error_count - 1, 0)))


class SidecueApp:
    def __init__(
        self,
        config: AppConfig,
        enable_overlay: bool = True,
        show_prompt: bool = False,
    ) -> None:
        self._config = config
        self._show_prompt = show_prompt
        self._stop_event = threading.Event()
        self._overlay = None
        self._retriever_lock = threading.Lock()
        self._pending: Queue[str] = Queue(maxsize=1)
        self._input_done = threading.Event()
        self._auto_close = threading.Event()
        self._asr_error = ""

        if enable_overlay:
            try:
                self._overlay = create_overlay(
                    config.ui,
                    on_close=self.stop,
                    on_documents_added=self.add_documents,
                    on_documents_removed=self.remove_documents,
                    on_text_submitted=self.submit_utterance,
                    engine_label=("Codex · Spark" if "spark" in config.llm.model and config.llm.provider == "codex" else config.llm.provider),
                )
            except Exception as exc:
                raise RuntimeError(
                    "Native window initialization failed; input and model were not started. Check UI dependencies. "
                    "Use --no-ui explicitly for console output."
                ) from exc

        self._transcript_source = None
        if self._overlay and config.asr.mode == "mic":
            self._overlay.set_status_provider(self._speech_status)
        self._llm = create_llm_client(config.llm)

        documents = load_documents(config.documents.paths)
        if not documents:
            raise RuntimeError("No usable sources found. Add TXT, MD, PDF, or DOCX files to the knowledge directory.")

        self._document_paths: list[str] = [doc.path for doc in documents]

        chunks = build_chunks(
            documents,
            chunk_chars=config.retrieval.chunk_chars,
            overlap_chars=config.retrieval.overlap_chars,
        )
        if not chunks:
            raise RuntimeError("Sources parsed, but no searchable text was found.")

        if config.retrieval.top_k < 0:
            raise ValueError("retrieval.top_k must not be negative.")

        self._retriever = KeywordRetriever(chunks)
        logger.info("Loaded sources: %d documents, %d chunks", len(documents), len(chunks))

        if self._overlay:
            self._overlay.publish_document_list(list(self._document_paths))

    def stop(self) -> None:
        self._stop_event.set()
        close = getattr(self._transcript_source, "close", None)
        if close:
            close()
        cancel = getattr(self._llm, "close", None)
        if cancel:
            cancel()

    def _speech_status(self) -> SpeechStatus:
        if self._asr_error:
            return SpeechStatus(state="Speech input stopped", detail=self._asr_error)
        if self._transcript_source:
            return self._transcript_source.status
        return SpeechStatus(state="Checking microphone and speech permissions")

    def submit_utterance(self, text: str) -> None:
        text = text.strip()
        if not text or self._stop_event.is_set():
            return
        # Replace a waiting segment, never interrupt a generation in progress.
        while True:
            try:
                self._pending.put_nowait(text)
                return
            except Full:
                try:
                    self._pending.get_nowait()
                except Empty:
                    pass

    def add_documents(self, paths: list[str]) -> None:
        new_paths = [p for p in paths if p not in self._document_paths]
        if not new_paths:
            return
        self._document_paths.extend(new_paths)
        threading.Thread(
            target=self._rebuild_retriever, daemon=True, name="doc-rebuild"
        ).start()

    def remove_documents(self, paths: list[str]) -> None:
        to_remove = set(paths)
        self._document_paths = [p for p in self._document_paths if p not in to_remove]
        threading.Thread(
            target=self._rebuild_retriever, daemon=True, name="doc-rebuild"
        ).start()

    def _rebuild_retriever(self) -> None:
        current_paths = list(self._document_paths)
        documents = load_documents(current_paths)
        if documents:
            chunks = build_chunks(
                documents,
                chunk_chars=self._config.retrieval.chunk_chars,
                overlap_chars=self._config.retrieval.overlap_chars,
            )
            new_retriever = KeywordRetriever(chunks) if chunks else KeywordRetriever([])
        else:
            new_retriever = KeywordRetriever([])
        with self._retriever_lock:
            self._retriever = new_retriever
        logger.info("Rebuilt retrieval index: %d documents", len(documents))
        if self._overlay:
            self._overlay.publish_document_list(list(self._document_paths))

    def _publish(self, update: OverlayUpdate) -> None:
        if self._overlay:
            self._overlay.publish(update)
            return

        print("\n" + "=" * 50)
        print(f"[INPUT]\n{update.transcript}")
        print(f"\n[PROMPTS]\n{update.suggestion}")
        if update.references:
            print(f"\n[SOURCES]\n{update.references}")
        if update.prompt:
            print(f"\n[Prompt]\n{update.prompt}")
        print("=" * 50 + "\n")

    def _run_loop(self) -> None:
        if self._transcript_source is None:
            if self._overlay and self._config.asr.mode == "stdin":
                self._input_done.set()
                return
            try:
                self._transcript_source = create_transcript_source(self._config.asr)
            except Exception as exc:
                logger.warning("Speech input initialization failed (%s)", type(exc).__name__)
                self._asr_error = str(exc)
                self._publish(OverlayUpdate("Speech input not started", str(exc), "", phase="input_error"))
                self._input_done.set()
                return
            if self._stop_event.is_set():
                self._transcript_source.close()
        consecutive_asr_errors = 0
        while not self._stop_event.is_set():
            try:
                logger.info("Waiting for input...")
                utterance = self._transcript_source.next_utterance()
            except Exception as exc:
                consecutive_asr_errors += 1
                if _is_fatal_asr_error(exc) or consecutive_asr_errors >= 3:
                    self._publish(
                        OverlayUpdate(
                            transcript="(ASR error)",
                            suggestion=(
                                f"Speech input failed: {exc}\n"
                                "Listening stopped after a non-recoverable error or three consecutive failures. Fix the configuration or environment, then restart."
                            ),
                            references="",
                            phase="input_error",
                        )
                    )
                    self._asr_error = str(exc)
                    close = getattr(self._transcript_source, "close", None)
                    if close:
                        close()
                    break

                retry_after = _asr_retry_delay_seconds(consecutive_asr_errors)
                self._publish(
                    OverlayUpdate(
                        transcript="(ASR error)",
                        suggestion=f"Speech input failed: {exc}\nRetrying in {retry_after:.1f}s.",
                        references="",
                        phase="input_error",
                    )
                )
                if self._stop_event.wait(timeout=retry_after):
                    break
                continue

            consecutive_asr_errors = 0
            if utterance is None:
                break

            utterance = utterance.strip()
            if not utterance:
                continue
            logger.info("Received input: chars=%d", len(utterance))
            # Keep recording during generation; only the newest waiting segment matters.
            self.submit_utterance(utterance)
        self._input_done.set()

    def _generate_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                utterance = self._pending.get(timeout=0.1)
            except Empty:
                if self._input_done.is_set() and not self._overlay:
                    break
                continue

            with self._retriever_lock:
                hits = self._retriever.search(utterance, top_k=self._config.retrieval.top_k)
            prompt = build_generation_prompt(
                system_prompt=self._config.llm.system_prompt,
                transcript=utterance,
                hits=hits,
            )
            self._publish(OverlayUpdate(
                transcript=utterance,
                suggestion="Generating prompts...",
                references="\n".join(hit.chunk.source_name for hit in hits),
                prompt=prompt if self._show_prompt or self._overlay else "",
                phase="generating",
            ))

            generation_started = time.monotonic()
            phase = "ready"
            try:
                suggestion = self._llm.generate(prompt)
                logger.info(
                    "Prompts generated: %.2fs, chars=%d",
                    time.monotonic() - generation_started,
                    len(suggestion),
                )
            except Exception as exc:
                logger.warning("Prompt generation failed (%s)", type(exc).__name__)
                suggestion = f"Model request failed: {exc}"
                phase = "error"

            if self._stop_event.is_set():
                break

            ref_lines = []
            for idx, hit in enumerate(hits, start=1):
                ref_lines.append(f"[{idx}] {hit.chunk.source_name} (score={hit.score:.3f})")

            self._publish(
                OverlayUpdate(
                    transcript=utterance,
                    suggestion=suggestion,
                    references="\n".join(ref_lines) if ref_lines else "No matching sources",
                    prompt=prompt if self._show_prompt or self._overlay else "",
                    phase=phase,
                    elapsed_seconds=time.monotonic() - generation_started,
                )
            )

    def run(self, duration_seconds: float | None = None) -> None:
        worker = threading.Thread(target=self._run_loop, daemon=True, name="sidecue-worker")
        generator = threading.Thread(target=self._generate_loop, daemon=True, name="sidecue-generator")
        timer = None
        if duration_seconds is not None:
            def timed_stop():
                self._auto_close.set()
                self.stop()
            timer = threading.Timer(duration_seconds, timed_stop)
            timer.daemon = True
            timer.start()
        worker.start()
        generator.start()
        try:
            if self._overlay:
                self._overlay.run(should_close=self._auto_close.is_set)
            else:
                while (worker.is_alive() or generator.is_alive()) and not self._stop_event.is_set():
                    worker.join(timeout=0.1)
                    generator.join(timeout=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            if timer:
                timer.cancel()
            self.stop()
            worker.join(timeout=1.5)
            generator.join(timeout=2)
            logger.info("Stopped; microphone and generation tasks closed")
