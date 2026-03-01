from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from sidecue.config import UiConfig
from sidecue.overlay import OverlayUpdate


@unittest.skipUnless(sys.platform == "darwin" and os.environ.get("RUN_GUI_TESTS") == "1", "Set RUN_GUI_TESTS=1 for native macOS controls")
class NativeOverlayTests(unittest.TestCase):
    def setUp(self):
        from sidecue.native_overlay import NativeOverlayWindow, A
        self.A = A
        self.closed = Mock()
        self.submitted = Mock()
        self.added = Mock()
        self.removed = Mock()
        self.window = NativeOverlayWindow(
            UiConfig("test", False, 440, 340, 36, 48), self.closed,
            self.added, self.removed, self.submitted,
        )

    def tearDown(self):
        self.window._close()

    def test_send_ignores_whitespace_and_clears_after_submission(self):
        window = self.window
        window._manual_input.setStringValue_("   ")
        window._refresh_send()
        self.assertFalse(window._send_button.isEnabled())
        window._submit_text()
        self.submitted.assert_not_called()
        window._manual_input.setStringValue_("  Meeting input  ")
        window._actions.controlTextDidChange_(None)
        self.assertTrue(window._send_button.isEnabled())
        window._actions.send_(None)
        self.submitted.assert_called_once_with("Meeting input")
        self.assertEqual(window._current_transcript, "Meeting input")
        self.assertEqual(window._manual_input.stringValue(), "")
        self.assertFalse(window._send_button.isEnabled())

    def test_documents_keep_full_paths_and_are_not_editable(self):
        window = self.window
        window._update_doc_list(["/first/notes.md", "/second/notes.md"])
        window._doc_table.selectRowIndexes_byExtendingSelection_(self.A.NSIndexSet.indexSetWithIndex_(1), False)
        window._document_selection_changed()
        window._actions.remove_(None)
        self.removed.assert_called_once_with(["/second/notes.md"])
        window._update_doc_list(["/second/notes.md"])
        self.assertEqual(window._selected_paths(), ["/second/notes.md"])
        for column in window._doc_table.tableColumns():
            self.assertFalse(column.isEditable())
        window._update_doc_list([])
        self.assertFalse(window._remove_button.isEnabled())
        self.assertFalse(window._doc_empty.isHidden())

    def test_file_picker_is_a_sheet_and_preserves_selected_paths(self):
        window = self.window
        panel = Mock()
        panel.URLs.return_value = [Mock(path=lambda: "/selected/new.md")]
        with patch("sidecue.native_overlay.A.NSOpenPanel") as picker:
            picker.openPanel.return_value = panel
            window._on_add_files()
            window._on_add_files()
            picker.openPanel.assert_called_once()
            completion = panel.beginSheetModalForWindow_completionHandler_.call_args.args[1]
            completion(self.A.NSModalResponseOK)
        self.added.assert_called_once_with(["/selected/new.md"])
        self.assertIsNone(window._file_panel)

    def test_queued_success_is_not_lost_to_next_generation_or_input_error(self):
        window = self.window
        window.publish(OverlayUpdate("first", "1. Prompt: first cue\nEvidence: first evidence", "first.md", "first prompt"))
        window.publish(OverlayUpdate("second", "", "second.md", "second prompt", phase="generating"))
        window.publish(OverlayUpdate("", "microphone error", "", phase="input_error"))
        window._poll()
        self.assertIn("first cue", window._hint_text.string())
        self.assertNotIn("first evidence", window._hint_text.string())
        self.assertIn("first.md", window._details_value())
        self.assertIn("first evidence", window._details_value())
        self.assertIn("Updating", window._status.stringValue())
        self.assertIn("Input error", window._status.stringValue())
        window._select_detail(1)
        self.assertEqual(window._detail_text.string(), "second prompt")

    def test_old_completion_does_not_overwrite_live_transcript(self):
        window = self.window
        window._apply_update(OverlayUpdate("old", "", "", phase="generating"))
        window._set_transcript("new live input")
        window._apply_update(OverlayUpdate("old", "1. cue", ""))
        self.assertEqual(window._current_transcript, "new live input")
        self.assertIn("FOR INPUT\nold", window._details_value())
        self.assertIn("LATEST INPUT\nnew live input", window._details_value())
        self.assertEqual(window._status.stringValue(), "Prompts for earlier input")

    def test_error_preserves_cues_and_exposes_detail(self):
        window = self.window
        window._apply_update(OverlayUpdate("first", "1. cue", "first.md"))
        window._apply_update(OverlayUpdate("second", "Model timeout", "", phase="error"))
        self.assertIn("cue", window._hint_text.string())
        self.assertEqual(window._status.toolTip(), "Model timeout")
        self.assertEqual(window._status.stringValue(), "Could not update prompts")
        self.assertIn("ERROR\nModel timeout", window._details_value())

    def test_copy_uses_original_content_and_pin_changes_window_level(self):
        window = self.window
        window._apply_update(OverlayUpdate("input", "1. Prompt: cue", "", "context"))
        with patch("sidecue.native_overlay.A.NSPasteboard") as pasteboard:
            window._copy_current()
            pasteboard.generalPasteboard().setString_forType_.assert_called_once_with("1. Prompt: cue", self.A.NSPasteboardTypeString)
            window._select_detail(1)
            window._copy_details()
            self.assertEqual(pasteboard.generalPasteboard().setString_forType_.call_args.args[0], "context")
        window._toggle_pin()
        self.assertEqual(window._window.level(), self.A.NSFloatingWindowLevel)
        self.assertEqual(window._pin_menu_item.state(), self.A.NSControlStateValueOn)
        window._toggle_pin()
        self.assertEqual(window._window.level(), self.A.NSNormalWindowLevel)
        self.assertEqual(window._pin_menu_item.state(), self.A.NSControlStateValueOff)

    def test_default_size_fits_three_cues_with_evidence_only_in_details(self):
        from sidecue.ui_preview import PREVIEW_SUGGESTION
        window = self.window
        window._apply_update(OverlayUpdate("input", PREVIEW_SUGGESTION, "refs"))
        used = window._hint_text.layoutManager().usedRectForTextContainer_(window._hint_text.textContainer())
        self.assertLessEqual(used.size.height, window._hint_scroll.contentSize().height)
        self.assertEqual(window._hint_text.string(),
                         "3 weeks for development\n2 days of design time\nTest / production mismatch")
        self.assertNotIn("Evidence:", window._hint_text.string())
        self.assertIn("Evidence:", window._details_value())
        self.assertTrue(window._status.isHidden())

    def test_native_toolbar_and_popovers_follow_system_appearance(self):
        window = self.window
        window._window.setContentSize_((380, 300))
        window._layout()
        self.assertIsNone(window._window.appearance())
        self.assertEqual(window._window.toolbarStyle(), self.A.NSWindowToolbarStyleUnifiedCompact)
        self.assertEqual([item.itemIdentifier() for item in window._toolbar.items()][1:],
                         ["sources", "details", "copy"])
        self.assertEqual(window._manual_input.bezelStyle(), self.A.NSTextFieldRoundedBezel)
        for name in ("sources", "details", "copy"):
            button = getattr(window, f"_{name}_button")
            self.assertTrue(button.image().isTemplate())
            self.assertTrue(button.accessibilityLabel())
        for popover in (window._sources_popover, window._details_popover):
            self.assertEqual(popover.behavior(), self.A.NSPopoverBehaviorTransient)
            self.assertFalse(popover.isShown())
        self.assertLessEqual(window._doc_heading.cell().cellSize().width,
                             window._doc_heading.frame().size.width)

    def test_context_disables_when_unavailable(self):
        window = self.window
        window._select_detail(1)
        self.assertEqual(window._detail_tab, 0)
        self.assertFalse(window._detail_tabs.isEnabledForSegment_(1))
        window._apply_update(OverlayUpdate("input", "1. cue", "", "context"))
        window._detail_tabs.setSelectedSegment_(1)
        window._actions.changeDetail_(window._detail_tabs)
        self.assertEqual(window._detail_text.string(), "context")
        window._apply_update(OverlayUpdate("input", "1. cue", ""))
        self.assertEqual(window._detail_tab, 0)
        self.assertIn("cue", window._detail_text.string())

    def test_speech_status_keeps_live_input_and_error_detail_available(self):
        window = self.window
        status = Mock(state="Listening", partial="Live words", detail="On-device", level_dbfs=-25)
        window.set_status_provider(lambda: status)
        window._poll()
        self.assertEqual(window._current_transcript, "Live words")
        self.assertEqual(window._status.stringValue(), "Listening")
        self.assertIn("Live words", window._details_value())
        self.assertIn("-25 dBFS", window._details_value())
        window.publish(OverlayUpdate("", "Recognition unavailable", "", phase="input_error"))
        window._poll()
        self.assertIn("Input error", window._status.stringValue())
        self.assertIn("Recognition unavailable", window._details_value())
        status.partial = "Recognition recovered"
        window._poll()
        self.assertEqual(window._status.stringValue(), "Listening")
        self.assertNotIn("Recognition unavailable", window._details_value())

    def test_layout_at_minimum_default_and_large_sizes(self):
        window = self.window
        window._apply_update(OverlayUpdate("Long input " * 60, "1. Prompt: " + "Long prompt " * 200, "refs" * 80, "context"))
        for width, height in [(380, 300), (440, 340), (820, 700)]:
            window._window.setContentSize_((width, height))
            window._layout()
            for view in [window._hint_scroll, window._status, window._manual_input, window._send_button]:
                frame = view.frame()
                with self.subTest(size=(width, height), view=str(type(view))):
                    self.assertGreaterEqual(frame.origin.x, 0)
                    self.assertGreaterEqual(frame.origin.y, 0)
                    self.assertLessEqual(frame.origin.x + frame.size.width, width)
                    self.assertLessEqual(frame.origin.y + frame.size.height, height)
            self.assertGreater(window._hint_scroll.contentSize().height, 100)
            self.assertGreater(window._hint_text.frame().size.height, window._hint_scroll.contentSize().height)

    def test_popover_content_fits_its_bounds(self):
        window = self.window
        for popover in (window._sources_popover, window._details_popover):
            parent = popover.contentViewController().view()
            width, height = parent.bounds().size
            for view in parent.subviews():
                frame = view.frame()
                self.assertGreaterEqual(frame.origin.x, 0)
                self.assertGreaterEqual(frame.origin.y, 0)
                self.assertLessEqual(frame.origin.x + frame.size.width, width)
                self.assertLessEqual(frame.origin.y + frame.size.height, height)

    def test_close_is_idempotent_and_cancels_callbacks(self):
        window = self.window
        callback = Mock()
        window.call_later(0, callback)
        window._close()
        window._close()
        window._poll()
        self.closed.assert_called_once()
        callback.assert_not_called()
        self.assertFalse(window._scheduled)

    def test_scheduled_callbacks_can_be_cancelled(self):
        window = self.window
        cancelled = Mock()
        completed = Mock()
        window.cancel_call(window.call_later(0, cancelled))
        window.call_later(0, completed)
        window._poll()
        cancelled.assert_not_called()
        completed.assert_called_once()

    def test_timed_preview_exits_without_a_user_event(self):
        result = subprocess.run([
            sys.executable, "-m", "sidecue", "--preview-ui", "--run-seconds", "0.3",
        ], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ObjCPointerWarning", result.stderr)

    def test_native_ui_connects_to_app_without_microphone_or_real_model(self):
        result = subprocess.run([
            sys.executable, "-m", "sidecue", "--asr-mode", "stdin",
            "--llm-provider", "mock", "--text", "What are the development constraints?", "--run-seconds", "1",
        ], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Native window initialization failed", result.stderr)
        self.assertIn("Prompts generated", result.stderr)
        self.assertIn("Stopped", result.stderr)
