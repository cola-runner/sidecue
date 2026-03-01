from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
import re
import time
from typing import Callable

import AppKit as A
import Quartz  # Register Core Graphics bridge metadata.
from Foundation import NSObject, NSTimer, NSMutableAttributedString, NSAttributedString
import objc

from .config import UiConfig
from .overlay import OverlayUpdate, SuggestionState, cue_lines


INK = A.NSColor.labelColor()
SECONDARY = A.NSColor.secondaryLabelColor()
ERROR = A.NSColor.systemRedColor()


class _Surface(A.NSView):
    def isFlipped(self):
        return True


class _Actions(NSObject):
    @objc.IBAction
    def tick_(self, sender):
        self.owner._poll()

    @objc.IBAction
    def send_(self, sender):
        self.owner._submit_text()

    @objc.IBAction
    def pin_(self, sender):
        self.owner._toggle_pin()

    @objc.IBAction
    def copy_(self, sender):
        self.owner._copy_current()

    @objc.IBAction
    def sources_(self, sender):
        self.owner._toggle_popover("sources")

    @objc.IBAction
    def details_(self, sender):
        self.owner._toggle_popover("details")

    @objc.IBAction
    def changeDetail_(self, sender):
        self.owner._select_detail(sender.selectedSegment())

    @objc.IBAction
    def copyDetails_(self, sender):
        self.owner._copy_details()

    @objc.IBAction
    def add_(self, sender):
        self.owner._on_add_files()

    @objc.IBAction
    def remove_(self, sender):
        self.owner._on_remove_files()

    @objc.IBAction
    def close_(self, sender):
        self.owner._close()

    def windowWillClose_(self, notification):
        self.owner._close(from_window=True)

    def windowDidResize_(self, notification):
        if hasattr(self.owner, "_manual_input"):
            self.owner._layout()

    def controlTextDidChange_(self, notification):
        self.owner._refresh_send()

    def toolbarAllowedItemIdentifiers_(self, toolbar):
        return [A.NSToolbarFlexibleSpaceItemIdentifier, "sources", "details", "copy"]

    def toolbarDefaultItemIdentifiers_(self, toolbar):
        return self.toolbarAllowedItemIdentifiers_(toolbar)

    def toolbar_itemForItemIdentifier_willBeInsertedIntoToolbar_(self, toolbar, identifier, inserted):
        return self.owner._toolbar_items.get(identifier)

    def numberOfRowsInTableView_(self, table):
        return len(self.owner._document_paths)

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        path = Path(self.owner._document_paths[row])
        return path.suffix.lstrip(".").upper() if column.identifier() == "type" else path.name

    def tableViewSelectionDidChange_(self, notification):
        self.owner._document_selection_changed()


class NativeOverlayWindow:
    """Main-thread AppKit UI; producers communicate through queues only."""

    def __init__(self, config: UiConfig, on_close: Callable[[], None],
                 on_documents_added=None, on_documents_removed=None,
                 on_text_submitted=None, engine_label: str = "Codex"):
        self._on_close = on_close
        self._on_documents_added = on_documents_added
        self._on_documents_removed = on_documents_removed
        self._on_text_submitted = on_text_submitted
        self._queue: Queue[OverlayUpdate] = Queue()
        self._doc_queue: Queue[list[str]] = Queue()
        self._state = SuggestionState()
        self._document_paths: list[str] = []
        self._current_transcript = ""
        self._last_partial = ""
        self._input_error = ""
        self._input_label = "Text input"
        self._engine_label = engine_label
        self._speech_state = ""
        self._speech_detail = ""
        self._status_provider = None
        self._closed = False
        self._running = False
        self._should_close = lambda: False
        self._timer = None
        self._scheduled: dict[int, tuple[float, Callable]] = {}
        self._next_call = 0
        self._copy_resets: dict[str, int] = {}
        self._file_panel = None
        self._pinned = config.always_on_top
        self._detail_tab = 0
        self._images = {}

        self._app = A.NSApplication.sharedApplication()
        self._app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
        self._actions = _Actions.alloc().init()
        self._actions.owner = self
        mask = (A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable |
                A.NSWindowStyleMaskMiniaturizable | A.NSWindowStyleMaskResizable)
        width, height = max(380, config.width), max(300, config.height)
        screen_height = A.NSScreen.mainScreen().visibleFrame().size.height
        rect = A.NSMakeRect(config.x, max(0, screen_height - config.y - height), width, height)
        self._window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, mask, A.NSBackingStoreBuffered, False)
        self._window.setTitle_(config.title)
        self._window.setBackgroundColor_(A.NSColor.windowBackgroundColor())
        self._window.setContentMinSize_((380, 300))
        self._window.setReleasedWhenClosed_(False)
        self._window.setDelegate_(self._actions)
        self._window.setLevel_(A.NSFloatingWindowLevel if self._pinned else A.NSNormalWindowLevel)

        self._root = _Surface.alloc().initWithFrame_(A.NSMakeRect(0, 0, width, height))
        self._window.setContentView_(self._root)
        self._hint_scroll, self._hint_text = self._scroll_text(self._root)
        self._status = self._label(self._root, "", 11, SECONDARY)
        self._status.setHidden_(True)
        self._spinner = A.NSProgressIndicator.alloc().init()
        self._spinner.setStyle_(A.NSProgressIndicatorStyleSpinning)
        self._spinner.setControlSize_(A.NSControlSizeSmall)
        self._spinner.setDisplayedWhenStopped_(False)
        self._root.addSubview_(self._spinner)

        self._manual_input = A.NSTextField.alloc().init()
        self._manual_input.setBezeled_(True)
        self._manual_input.setBezelStyle_(A.NSTextFieldRoundedBezel)
        self._manual_input.setFont_(A.NSFont.systemFontOfSize_(13))
        self._manual_input.setPlaceholderString_("Add context...")
        self._manual_input.setAccessibilityLabel_("Add context")
        self._manual_input.setDelegate_(self._actions)
        self._manual_input.setTarget_(self._actions)
        self._manual_input.setAction_("send:")
        self._root.addSubview_(self._manual_input)
        self._send_button = self._button(self._root, "arrow-up", "Submit text", "send:")
        self._send_button.setBezelStyle_(A.NSBezelStyleCircular)
        self._send_button.setContentTintColor_(A.NSColor.controlAccentColor())
        self._refresh_send()

        self._build_toolbar()
        self._build_documents()
        self._build_details()
        self._install_menu()
        self._set_plain_text(self._hint_text, "No prompts yet", size=20, foreground=SECONDARY)
        self._window.setContentSize_((width, height))
        self._layout()

    def _label(self, parent, text, size=12, foreground=INK):
        label = A.NSTextField.labelWithString_(text)
        label.setFont_(A.NSFont.systemFontOfSize_(size))
        label.setTextColor_(foreground)
        label.setLineBreakMode_(A.NSLineBreakByTruncatingTail)
        parent.addSubview_(label)
        return label

    def _image(self, name):
        if name not in self._images:
            path = Path(__file__).parent / "assets" / f"{name}-ink.png"
            image = A.NSImage.alloc().initWithContentsOfFile_(str(path))
            if image is None:
                raise RuntimeError(f"Missing UI icon: {path.name}")
            image.setSize_((16, 16))
            image.setTemplate_(True)
            self._images[name] = image
        return self._images[name]

    def _button(self, parent, name, title, action):
        button = A.NSButton.alloc().initWithFrame_(A.NSMakeRect(0, 0, 30, 28))
        button.setTitle_("")
        button.setImage_(self._image(name))
        button.setImagePosition_(A.NSImageOnly)
        button.setBezelStyle_(A.NSBezelStyleTexturedRounded)
        button.setButtonType_(A.NSButtonTypeMomentaryPushIn)
        button.setToolTip_(title)
        button.setAccessibilityLabel_(title)
        button.setTarget_(self._actions)
        button.setAction_(action)
        if parent is not None:
            parent.addSubview_(button)
        return button

    def _scroll_text(self, parent):
        scroll = A.NSScrollView.alloc().init()
        scroll.setBorderType_(A.NSNoBorder)
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setScrollerStyle_(A.NSScrollerStyleOverlay)
        text = A.NSTextView.alloc().initWithFrame_(A.NSMakeRect(0, 0, 400, 200))
        text.setEditable_(False)
        text.setSelectable_(True)
        text.setDrawsBackground_(False)
        text.setTextContainerInset_((0, 0))
        text.textContainer().setLineFragmentPadding_(0)
        text.setVerticallyResizable_(True)
        text.setHorizontallyResizable_(False)
        text.textContainer().setWidthTracksTextView_(True)
        scroll.setDocumentView_(text)
        parent.addSubview_(scroll)
        return scroll, text

    def _build_toolbar(self):
        self._toolbar_items = {}
        for identifier, image, title, action in (
            ("sources", "file-text", "Sources", "sources:"),
            ("details", "info", "Prompt details", "details:"),
            ("copy", "copy", "Copy prompts", "copy:"),
        ):
            button = self._button(None, image, title, action)
            item = A.NSToolbarItem.alloc().initWithItemIdentifier_(identifier)
            item.setLabel_(title)
            item.setPaletteLabel_(title)
            item.setView_(button)
            item.setMinSize_((30, 28))
            item.setMaxSize_((30, 28))
            self._toolbar_items[identifier] = item
            setattr(self, f"_{identifier}_button", button)
        self._copy_button.setEnabled_(False)
        self._toolbar = A.NSToolbar.alloc().initWithIdentifier_("Sidecue.Toolbar")
        self._toolbar.setDelegate_(self._actions)
        self._toolbar.setDisplayMode_(A.NSToolbarDisplayModeIconOnly)
        self._toolbar.setAllowsUserCustomization_(False)
        self._toolbar.setAutosavesConfiguration_(False)
        self._toolbar.setShowsBaselineSeparator_(False)
        self._window.setToolbarStyle_(A.NSWindowToolbarStyleUnifiedCompact)
        self._window.setToolbar_(self._toolbar)

    @staticmethod
    def _popover(width, height):
        view = _Surface.alloc().initWithFrame_(A.NSMakeRect(0, 0, width, height))
        controller = A.NSViewController.alloc().init()
        controller.setView_(view)
        popover = A.NSPopover.alloc().init()
        popover.setBehavior_(A.NSPopoverBehaviorTransient)
        popover.setContentViewController_(controller)
        popover.setContentSize_((width, height))
        return popover, view

    def _build_documents(self):
        self._sources_popover, page = self._popover(340, 270)
        self._doc_heading = self._label(page, "0 sources", 12, SECONDARY)
        self._frame(self._doc_heading, 20, 20, 216, 20)
        self._add_button = self._button(page, "plus", "Add sources", "add:")
        self._frame(self._add_button, 249, 15, 30, 28)
        self._add_button.setEnabled_(self._on_documents_added is not None)
        self._remove_button = self._button(page, "trash-2", "Remove selected sources", "remove:")
        self._frame(self._remove_button, 286, 15, 30, 28)
        self._remove_button.setEnabled_(False)
        self._doc_scroll = A.NSScrollView.alloc().init()
        self._doc_scroll.setDrawsBackground_(False)
        self._doc_scroll.setHasVerticalScroller_(True)
        self._doc_scroll.setAutohidesScrollers_(True)
        self._doc_scroll.setScrollerStyle_(A.NSScrollerStyleOverlay)
        self._doc_table = A.NSTableView.alloc().init()
        self._doc_table.setHeaderView_(None)
        self._doc_table.setBackgroundColor_(A.NSColor.clearColor())
        self._doc_table.setRowHeight_(34)
        self._doc_table.setIntercellSpacing_((8, 0))
        self._doc_table.setAllowsMultipleSelection_(True)
        self._doc_table.setDataSource_(self._actions)
        self._doc_table.setDelegate_(self._actions)
        for name, width in (("name", 242), ("type", 46)):
            column = A.NSTableColumn.alloc().initWithIdentifier_(name)
            column.setWidth_(width)
            column.setEditable_(False)
            column.dataCell().setEditable_(False)
            column.dataCell().setFont_(A.NSFont.systemFontOfSize_(13 if name == "name" else 10))
            column.dataCell().setTextColor_(INK if name == "name" else SECONDARY)
            column.dataCell().setLineBreakMode_(A.NSLineBreakByTruncatingMiddle)
            self._doc_table.addTableColumn_(column)
        self._doc_scroll.setDocumentView_(self._doc_table)
        page.addSubview_(self._doc_scroll)
        self._frame(self._doc_scroll, 16, 56, 308, 174)
        self._doc_empty = self._label(page, "No sources added", 13, SECONDARY)
        self._frame(self._doc_empty, 24, 78, 292, 20)
        self._doc_detail = self._label(page, "", 10, SECONDARY)
        self._doc_detail.setLineBreakMode_(A.NSLineBreakByTruncatingMiddle)
        self._frame(self._doc_detail, 20, 241, 300, 16)

    def _build_details(self):
        self._details_popover, page = self._popover(360, 340)
        self._detail_tabs = A.NSSegmentedControl.alloc().initWithFrame_(A.NSMakeRect(20, 18, 224, 26))
        self._detail_tabs.setSegmentCount_(2)
        for index, title in enumerate(("Evidence", "Context")):
            self._detail_tabs.setLabel_forSegment_(title, index)
            self._detail_tabs.setWidth_forSegment_(105, index)
        self._detail_tabs.setSelectedSegment_(0)
        self._detail_tabs.setEnabled_forSegment_(False, 1)
        self._detail_tabs.setTarget_(self._actions)
        self._detail_tabs.setAction_("changeDetail:")
        page.addSubview_(self._detail_tabs)
        self._detail_copy_button = self._button(page, "copy", "Copy details", "copyDetails:")
        self._detail_copy_button.setEnabled_(False)
        self._frame(self._detail_copy_button, 310, 17, 30, 28)
        self._detail_scroll, self._detail_text = self._scroll_text(page)
        self._frame(self._detail_scroll, 20, 62, 320, 258)
        self._refresh_details()

    def _toggle_popover(self, name):
        popover = getattr(self, f"_{name}_popover")
        if popover.isShown():
            popover.close()
            return
        for other in (self._sources_popover, self._details_popover):
            other.close()
        if name == "details":
            self._refresh_details()
        button = getattr(self, f"_{name}_button")
        popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, A.NSMinYEdge)

    def _details_value(self):
        if self._detail_tab == 1:
            return self._state.prompt
        sections = []
        errors = "\n".join(filter(None, (self._state.error, self._input_error)))
        if errors:
            sections.append(f"ERROR\n{errors}")
        if self._state.suggestion:
            sections.append(f"PROMPTS & EVIDENCE\n{self._state.suggestion}")
        if self._state.transcript:
            sections.append(f"FOR INPUT\n{self._state.transcript}")
        if self._current_transcript and self._current_transcript != self._state.transcript:
            sections.append(f"LATEST INPUT\n{self._current_transcript}")
        if self._state.references:
            references = re.sub(r"\s*\(score=[^)]+\)", "", self._state.references)
            sections.append(f"SOURCES\n{references}")
        if self._speech_detail:
            sections.append(f"INPUT STATUS\n{self._speech_detail}")
        if sections:
            sections.append(f"{self._engine_label} / {self._input_label}")
        return "\n\n".join(sections)

    def _refresh_details(self):
        if self._detail_tab == 1 and not self._state.prompt:
            self._detail_tab = 0
        self._detail_tabs.setSelectedSegment_(self._detail_tab)
        self._detail_tabs.setEnabled_forSegment_(bool(self._state.prompt), 1)
        value = self._details_value()
        self._set_plain_text(self._detail_text, value or "No details yet")
        self._detail_copy_button.setEnabled_(bool(value))
        self._layout_text(self._detail_scroll, self._detail_text)

    def _select_detail(self, index):
        self._detail_tab = index if index == 0 or self._state.prompt else 0
        self._refresh_details()
        self._detail_text.scrollRangeToVisible_((0, 0))

    def _install_menu(self):
        menu = A.NSMenu.alloc().init()
        for title, entries in (
            ("Sidecue", [("Quit Sidecue", "close:", "q", self._actions)]),
            ("Edit", [("Cut", "cut:", "x", None), ("Copy", "copy:", "c", None),
                     ("Paste", "paste:", "v", None), ("Select All", "selectAll:", "a", None)]),
            ("Window", [("Sources", "sources:", "", self._actions),
                       ("Prompt Details", "details:", "", self._actions),
                       ("Keep on Top", "pin:", "", self._actions),
                       ("Close", "close:", "w", self._actions)]),
        ):
            item = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
            submenu = A.NSMenu.alloc().initWithTitle_(title)
            for name, selector, key, target in entries:
                command = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(name, selector, key)
                if target is not None:
                    command.setTarget_(target)
                if selector == "pin:":
                    self._pin_menu_item = command
                    command.setState_(A.NSControlStateValueOn if self._pinned else A.NSControlStateValueOff)
                submenu.addItem_(command)
            item.setSubmenu_(submenu)
            menu.addItem_(item)
        self._app.setMainMenu_(menu)

    @staticmethod
    def _frame(view, x, y, width, height):
        view.setFrame_(A.NSMakeRect(x, y, max(1, width), max(1, height)))

    def _layout_text(self, scroll, text):
        size = scroll.contentSize()
        text.textContainer().setContainerSize_((size.width, 1000000))
        text.setFrameSize_((size.width, size.height))
        manager = text.layoutManager()
        manager.ensureLayoutForTextContainer_(text.textContainer())
        needed = manager.usedRectForTextContainer_(text.textContainer()).size.height
        text.setFrameSize_((size.width, max(size.height, needed + 4)))

    def _layout(self):
        width, height = self._root.bounds().size
        self._frame(self._hint_scroll, 28, 28, width - 56, height - 120)
        self._layout_text(self._hint_scroll, self._hint_text)
        generating = self._state.phase == "generating"
        self._frame(self._spinner, 25, height - 82, 14, 14)
        self._frame(self._status, 45 if generating else 25, height - 82,
                    width - (70 if generating else 50), 16)
        self._frame(self._manual_input, 24, height - 51, width - 90, 30)
        self._frame(self._send_button, width - 54, height - 52, 32, 32)

    def _set_plain_text(self, view, value, size=12, foreground=INK):
        style = A.NSMutableParagraphStyle.alloc().init()
        style.setLineSpacing_(3)
        attributes = {A.NSFontAttributeName: A.NSFont.systemFontOfSize_(size),
                      A.NSForegroundColorAttributeName: foreground,
                      A.NSParagraphStyleAttributeName: style}
        string = NSAttributedString.alloc().initWithString_attributes_(value, attributes)
        view.textStorage().setAttributedString_(string)

    def _render_cues(self, value):
        text = NSMutableAttributedString.alloc().init()
        lines = [(kind, content) for kind, content in cue_lines(value) if kind != "evidence"]
        for position, (kind, content) in enumerate(lines):
            style = A.NSMutableParagraphStyle.alloc().init()
            style.setLineSpacing_(3)
            style.setParagraphSpacing_(18 if kind == "cue" else 8)
            suffix = "\n" if position < len(lines) - 1 else ""
            font = (A.NSFont.systemFontOfSize_weight_(20, A.NSFontWeightMedium)
                    if kind == "cue" else A.NSFont.systemFontOfSize_(15))
            text.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(content + suffix, {
                A.NSFontAttributeName: font, A.NSForegroundColorAttributeName: INK,
                A.NSParagraphStyleAttributeName: style,
            }))
        self._hint_text.textStorage().setAttributedString_(text)
        self._hint_text.scrollRangeToVisible_((0, 0))

    def _toggle_pin(self):
        self._pinned = not self._pinned
        self._window.setLevel_(A.NSFloatingWindowLevel if self._pinned else A.NSNormalWindowLevel)
        self._pin_menu_item.setState_(A.NSControlStateValueOn if self._pinned else A.NSControlStateValueOff)

    def _copy_value(self, value, button, key):
        if not value:
            return
        board = A.NSPasteboard.generalPasteboard()
        board.clearContents()
        board.setString_forType_(value, A.NSPasteboardTypeString)
        button.setImage_(self._image("check"))
        self.cancel_call(self._copy_resets.get(key))
        self._copy_resets[key] = self.call_later(1.2, lambda: button.setImage_(self._image("copy")))

    def _copy_current(self):
        self._copy_value(self._state.suggestion, self._copy_button, "prompts")

    def _copy_details(self):
        self._copy_value(self._details_value(), self._detail_copy_button, "details")

    def _refresh_send(self):
        self._send_button.setEnabled_(bool(self._manual_input.stringValue().strip() and self._on_text_submitted))

    def _submit_text(self):
        value = self._manual_input.stringValue().strip()
        if value and self._on_text_submitted:
            self._on_text_submitted(value)
            self._manual_input.setStringValue_("")
            self._refresh_send()
            self._input_error = ""
            self._set_transcript(value)

    def _on_add_files(self):
        if self._file_panel:
            return
        self._sources_popover.close()
        panel = A.NSOpenPanel.openPanel()
        self._file_panel = panel
        panel.setTitle_("Add sources")
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(True)
        panel.setAllowedFileTypes_(["txt", "md", "pdf", "docx"])

        def complete(response):
            self._file_panel = None
            if not self._closed and response == A.NSModalResponseOK and self._on_documents_added:
                self._on_documents_added([str(url.path()) for url in panel.URLs()])

        panel.beginSheetModalForWindow_completionHandler_(self._window, complete)

    def _selected_paths(self):
        selected = self._doc_table.selectedRowIndexes()
        return [path for index, path in enumerate(self._document_paths) if selected.containsIndex_(index)]

    def _document_selection_changed(self):
        paths = self._selected_paths()
        self._remove_button.setEnabled_(bool(paths and self._on_documents_removed))
        self._doc_detail.setStringValue_(paths[0] if len(paths) == 1 else f"{len(paths)} sources selected" if paths else "")
        self._doc_detail.setToolTip_("\n".join(paths))

    def _on_remove_files(self):
        paths = self._selected_paths()
        if paths and self._on_documents_removed:
            self._on_documents_removed(paths)

    def _update_doc_list(self, paths):
        selected = set(self._selected_paths())
        self._document_paths = list(paths)
        self._doc_table.reloadData()
        indexes = A.NSMutableIndexSet.indexSet()
        for index, path in enumerate(paths):
            if path in selected:
                indexes.addIndex_(index)
        self._doc_table.selectRowIndexes_byExtendingSelection_(indexes, False)
        self._doc_heading.setStringValue_(f"{len(paths)} source" + ("s" if len(paths) != 1 else ""))
        self._doc_empty.setHidden_(bool(paths))
        self._document_selection_changed()

    def _set_transcript(self, value):
        self._current_transcript = value
        self._refresh_status()
        self._refresh_details()

    def _apply_update(self, update):
        if update.phase == "input_error":
            self._input_error = update.suggestion
            self._refresh_status()
            self._refresh_details()
            return
        self._state.apply(update, time.monotonic())
        if update.phase == "generating" or not self._current_transcript:
            self._set_transcript(update.transcript)
        if update.phase == "ready":
            self._render_cues(self._state.suggestion)
        elif not self._state.suggestion:
            self._set_plain_text(self._hint_text, "No prompts yet", size=20, foreground=SECONDARY)
        self._copy_button.setEnabled_(bool(self._state.suggestion))
        if update.phase == "generating":
            self._spinner.startAnimation_(None)
        else:
            self._spinner.stopAnimation_(None)
        self._refresh_status()
        self._refresh_details()
        self._layout()

    def _refresh_status(self):
        if self._state.phase == "generating":
            value = self._state.status(time.monotonic()).replace("Generating", "Updating")
        elif self._state.error:
            value = "Could not update prompts"
        elif self._state.suggestion and self._state.transcript != self._current_transcript:
            value = "Prompts for earlier input"
        else:
            value = self._speech_state
        if self._input_error:
            value = f"{value} / Input error" if value else "Input error"
        self._status.setStringValue_(value)
        self._status.setHidden_(not value)
        self._status.setTextColor_(ERROR if self._state.error or self._input_error else SECONDARY)
        self._status.setToolTip_("\n".join(filter(None, (self._state.error, self._input_error, self._speech_detail))))

    def _refresh_speech_status(self):
        if not self._status_provider:
            return
        status = self._status_provider()
        self._input_label = "Microphone input"
        self._speech_state = status.state
        self._speech_detail = "\n".join(filter(None, (status.state, status.detail, f"Input level: {status.level_dbfs:.0f} dBFS")))
        if status.partial and status.partial != self._last_partial:
            self._last_partial = status.partial
            self._input_error = ""
            self._set_transcript(status.partial)
        self._refresh_status()
        if self._details_popover.isShown():
            self._refresh_details()

    def set_input_label(self, value):
        self._input_label = value

    def set_status_provider(self, provider):
        self._status_provider = provider

    def publish(self, update):
        self._queue.put(update)

    def publish_document_list(self, paths):
        self._doc_queue.put(list(paths))

    def call_later(self, delay, callback):
        self._next_call += 1
        self._scheduled[self._next_call] = (time.monotonic() + delay, callback)
        return self._next_call

    def cancel_call(self, token):
        self._scheduled.pop(token, None)

    def _poll(self):
        if self._closed:
            return
        if self._should_close():
            self._close()
            return
        while True:
            try:
                self._apply_update(self._queue.get_nowait())
            except Empty:
                break
        latest_paths = None
        while True:
            try:
                latest_paths = self._doc_queue.get_nowait()
            except Empty:
                break
        if latest_paths is not None:
            self._update_doc_list(latest_paths)
        self._refresh_speech_status()
        self._refresh_status()
        now = time.monotonic()
        for token, (deadline, callback) in list(self._scheduled.items()):
            if deadline <= now and token in self._scheduled:
                del self._scheduled[token]
                callback()

    def _close(self, from_window=False):
        if self._closed:
            return
        self._closed = True
        if self._timer:
            self._timer.invalidate()
        if self._file_panel:
            self._file_panel.cancel_(None)
        self._sources_popover.close()
        self._details_popover.close()
        self._scheduled.clear()
        self._spinner.stopAnimation_(None)
        try:
            self._on_close()
        finally:
            if not from_window:
                self._window.close()
            if self._running:
                self._app.stop_(None)
                # stop() does not wake an event loop stopped from an NSTimer.
                event = A.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                    A.NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0)
                self._app.postEvent_atStart_(event, True)

    def run(self, should_close=None):
        self._should_close = should_close or (lambda: False)
        self._poll()
        if self._closed:
            return
        self._window.makeKeyAndOrderFront_(None)
        self._window.makeFirstResponder_(None)
        self._app.activateIgnoringOtherApps_(True)
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.25, self._actions, "tick:", None, True)
        self._running = True
        try:
            self._app.run()
        finally:
            self._running = False
            self._close()
