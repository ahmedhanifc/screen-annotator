# SPDX-License-Identifier: MIT
"""Settings dialog.

For now it edits just the global toggle hotkey — the one setting a user genuinely
needs to change (to resolve a conflict or pick their own chord). Tool, colour and
size are remembered automatically from your last use, so they aren't repeated
here."""

from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QKeySequenceEdit, QLabel, QVBoxLayout,
)

from .config import format_hotkey, parse_hotkey


def _spec_from_sequence(seq: QKeySequence):
    """Convert a captured QKeySequence to our 'ctrl+alt+a' chord spec, or None.

    Only the first key combination is used; Qt's portable text ('Ctrl+Alt+A',
    'Meta' for Super/Win) lowercases straight into parse_hotkey's vocabulary."""
    if seq.isEmpty():
        return None
    text = seq.toString(QKeySequence.SequenceFormat.PortableText)
    first = text.split(",")[0].strip()
    return first.lower() or None


def _is_valid(spec):
    """A usable global hotkey: at least one modifier (so we don't steal a bare
    key from apps) and a single letter/digit main key (what both backends'
    hotkey registration supports)."""
    parsed = parse_hotkey(spec) if spec else None
    if not parsed:
        return False
    key, mods = parsed
    if not mods:
        return False
    return len(key) == 1 and (key.isalpha() or key.isdigit())


class SettingsDialog(QDialog):
    def __init__(self, current_spec, parent=None):
        super().__init__(parent)
        self.setWindowTitle("screen-annotator — Settings")
        self._spec = current_spec

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Global toggle hotkey</b>"))
        layout.addWidget(QLabel(
            "Click the field and press the key combination that shows and hides "
            "the overlay."))

        self._edit = QKeySequenceEdit()
        parsed = parse_hotkey(current_spec)
        if parsed:
            self._edit.setKeySequence(QKeySequence(format_hotkey(current_spec)))
        try:
            self._edit.setMaximumSequenceLength(1)   # single chord (Qt 6.5+)
        except (AttributeError, TypeError):
            pass
        self._edit.keySequenceChanged.connect(self._on_changed)
        layout.addWidget(self._edit)

        self._hint = QLabel("")
        self._hint.setStyleSheet("color: #b04040;")
        layout.addWidget(self._hint)

        layout.addWidget(QLabel(
            "<i>Your tool, colour and size are remembered automatically.</i>"))

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._on_changed()

    def _on_changed(self, *_):
        spec = _spec_from_sequence(self._edit.keySequence())
        valid = _is_valid(spec)
        self._buttons.button(
            QDialogButtonBox.StandardButton.Ok).setEnabled(valid)
        if spec and not valid:
            self._hint.setText(
                "Use at least one modifier (Ctrl / Alt / Shift / Super) plus a "
                "single letter or number.")
        else:
            self._hint.setText("")

    def hotkey_spec(self):
        """The chosen chord spec (validated) once the dialog is accepted."""
        return _spec_from_sequence(self._edit.keySequence())
