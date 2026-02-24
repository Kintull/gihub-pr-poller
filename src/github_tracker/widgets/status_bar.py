"""Status bar widget showing keybinding hints."""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static
from textual.app import ComposeResult


class StatusBar(Widget):
    """Bottom status bar with keyboard shortcut hints."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }

    StatusBar Static {
        width: 1fr;
    }
    """

    HINTS = " ↑↓ Navigate │ Tab: Switch table │ Enter: Open PR │ J: Open Jira │ r: Refresh │ q: Quit │ ?: Help "

    def compose(self) -> ComposeResult:
        yield Static(self.HINTS, id="status-hints")
