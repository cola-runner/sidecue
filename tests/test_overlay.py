from __future__ import annotations

import unittest

from sidecue.overlay import OverlayUpdate, SuggestionState, cue_lines


class SuggestionStateTests(unittest.TestCase):
    def setUp(self):
        self.state = SuggestionState()
        self.ready = OverlayUpdate("First input", "First cue", "[1] a.md", "first prompt", elapsed_seconds=1.25)

    def test_idle_and_real_elapsed_time(self):
        self.assertEqual(self.state.status(0), "Waiting for input")
        self.state.apply(OverlayUpdate("Question", "", "", phase="generating"), now=0)
        self.assertEqual(self.state.status(8.9), "Generating · 8s")
        self.state.apply(self.ready, now=9)
        self.assertEqual(self.state.status(40), "Ready · 1.2s")

    def test_next_request_keeps_previous_answer_and_its_sources(self):
        self.state.apply(self.ready, now=0)
        self.state.apply(OverlayUpdate("Second input", "Generating", "b.md", "second prompt", phase="generating"), now=2)
        self.assertEqual(self.state.suggestion, "First cue")
        self.assertEqual(self.state.transcript, "First input")
        self.assertEqual(self.state.references, "[1] a.md")
        self.assertEqual(self.state.prompt, "second prompt")

    def test_generation_failure_keeps_answer_and_stops_clock(self):
        self.state.apply(self.ready, now=0)
        self.state.apply(OverlayUpdate("Second input", "Request timed out", "", phase="error"), now=20)
        self.assertEqual(self.state.suggestion, "First cue")
        self.assertEqual(self.state.error, "Request timed out")
        self.assertIsNone(self.state.started_at)
        self.assertEqual(self.state.status(30), "Generation failed")

    def test_input_failure_cannot_replace_generation(self):
        self.state.apply(self.ready, now=0)
        before = vars(self.state).copy()
        self.state.apply(OverlayUpdate("", "Microphone permission denied", "", phase="input_error"), now=5)
        self.assertEqual(vars(self.state), before)

    def test_success_replaces_stale_answer_and_clears_error(self):
        self.state.apply(OverlayUpdate("", "Timed out", "", phase="error"), now=0)
        self.state.apply(self.ready, now=3)
        self.assertEqual(self.state.error, "")
        self.assertEqual(self.state.suggestion, "First cue")



class CueFormattingTests(unittest.TestCase):
    def test_english_labels_separate_prompts_from_evidence(self):
        self.assertEqual(cue_lines("1. Prompt: Three weeks\nEvidence: Delivery plan [1]\n2. **Cue:** Two days\nevidence: Design capacity [2]"), [
            ("cue", "Three weeks"), ("evidence", "Evidence: Delivery plan [1]"),
            ("cue", "Two days"), ("evidence", "evidence: Design capacity [2]"),
        ])

    def test_evidence_is_not_numbered_as_a_separate_cue(self):
        self.assertEqual(cue_lines("1. Prompt: Three weeks\n   Evidence: Delivery plan [1]\n2. **Cue**: Configuration gap\n   Evidence: Environment notes [2]"), [
            ("cue", "Three weeks"), ("evidence", "Evidence: Delivery plan [1]"),
            ("cue", "Configuration gap"), ("evidence", "Evidence: Environment notes [2]"),
        ])

    def test_continuations_are_not_extra_cues(self):
        self.assertEqual(cue_lines("A cue\nAdditional context\n\n- Another cue"), [
            ("cue", "A cue"), ("continuation", "Additional context"),
            ("cue", "Another cue"),
        ])

    def test_empty_input_has_no_cues(self):
        self.assertEqual(cue_lines(" \n "), [])
