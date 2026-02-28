#!/usr/bin/env python3
"""
Umlaut Config Manager - GUI for managing keyboard remapping configs
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, GObject, GdkPixbuf, Pango
import json
import os
import subprocess
from pathlib import Path
import re
import signal

from umlaut_paths import (
    SYSTEM_ICON_DIR, USER_CONFIG_DIR,
    USER_ICON_DIR, USER_SETTINGS as SETTINGS_PATH,
    load_settings, save_settings,
    APPLET_SCRIPT, TEST_MODE_FILE
)

# Paths imported from umlaut_paths


# ---------------------------------------------------------------------------
# Key capture widgets (merged from key_capture_dialog.py)
# ---------------------------------------------------------------------------

EVDEV_TO_NAME = {
    1:  'KEY_ESC',        2:  'KEY_1',           3:  'KEY_2',
    4:  'KEY_3',          5:  'KEY_4',            6:  'KEY_5',
    7:  'KEY_6',          8:  'KEY_7',            9:  'KEY_8',
    10: 'KEY_9',          11: 'KEY_0',            12: 'KEY_MINUS',
    13: 'KEY_EQUAL',      14: 'KEY_BACKSPACE',    15: 'KEY_TAB',
    16: 'KEY_Q',          17: 'KEY_W',            18: 'KEY_E',
    19: 'KEY_R',          20: 'KEY_T',            21: 'KEY_Y',
    22: 'KEY_U',          23: 'KEY_I',            24: 'KEY_O',
    25: 'KEY_P',          26: 'KEY_LEFTBRACE',    27: 'KEY_RIGHTBRACE',
    28: 'KEY_ENTER',      29: 'KEY_LEFTCTRL',     30: 'KEY_A',
    31: 'KEY_S',          32: 'KEY_D',            33: 'KEY_F',
    34: 'KEY_G',          35: 'KEY_H',            36: 'KEY_J',
    37: 'KEY_K',          38: 'KEY_L',            39: 'KEY_SEMICOLON',
    40: 'KEY_APOSTROPHE', 41: 'KEY_GRAVE',        42: 'KEY_LEFTSHIFT',
    43: 'KEY_BACKSLASH',  44: 'KEY_Z',            45: 'KEY_X',
    46: 'KEY_C',          47: 'KEY_V',            48: 'KEY_B',
    49: 'KEY_N',          50: 'KEY_M',            51: 'KEY_COMMA',
    52: 'KEY_DOT',        53: 'KEY_SLASH',        54: 'KEY_RIGHTSHIFT',
    55: 'KEY_KPASTERISK', 56: 'KEY_LEFTALT',      57: 'KEY_SPACE',
    58: 'KEY_CAPSLOCK',   59: 'KEY_F1',           60: 'KEY_F2',
    61: 'KEY_F3',         62: 'KEY_F4',           63: 'KEY_F5',
    64: 'KEY_F6',         65: 'KEY_F7',           66: 'KEY_F8',
    67: 'KEY_F9',         68: 'KEY_F10',          87: 'KEY_F11',
    88: 'KEY_F12',        97: 'KEY_RIGHTCTRL',    100: 'KEY_RIGHTALT',
    102: 'KEY_HOME',      103: 'KEY_UP',          104: 'KEY_PAGEUP',
    105: 'KEY_LEFT',      106: 'KEY_RIGHT',       107: 'KEY_END',
    108: 'KEY_DOWN',      109: 'KEY_PAGEDOWN',    110: 'KEY_INSERT',
    111: 'KEY_DELETE',    125: 'KEY_LEFTMETA',    126: 'KEY_RIGHTMETA',
}

BOTH_PAIRS = {
    'KEY_LEFTALT':    'KEY_RIGHTALT',
    'KEY_RIGHTALT':   'KEY_LEFTALT',
    'KEY_LEFTCTRL':   'KEY_RIGHTCTRL',
    'KEY_RIGHTCTRL':  'KEY_LEFTCTRL',
    'KEY_LEFTSHIFT':  'KEY_RIGHTSHIFT',
    'KEY_RIGHTSHIFT': 'KEY_LEFTSHIFT',
    'KEY_LEFTMETA':   'KEY_RIGHTMETA',
    'KEY_RIGHTMETA':  'KEY_LEFTMETA',
}

# Keys that should NOT be used as compose/target (pure modifiers)
MODIFIER_EVDEV = set(BOTH_PAIRS.keys())

# Human-readable display names for keys without a printable character
DISPLAY_NAMES = {
    'KEY_ESC': 'Esc',         'KEY_TAB': 'Tab',        'KEY_ENTER': 'Enter',
    'KEY_BACKSPACE': 'Bksp',  'KEY_DELETE': 'Del',     'KEY_INSERT': 'Ins',
    'KEY_HOME': 'Home',       'KEY_END': 'End',         'KEY_PAGEUP': 'PgUp',
    'KEY_PAGEDOWN': 'PgDn',   'KEY_UP': '↑',            'KEY_DOWN': '↓',
    'KEY_LEFT': '←',          'KEY_RIGHT': '→',          'KEY_SPACE': 'Space',
    'KEY_LEFTALT': 'LAlt',    'KEY_RIGHTALT': 'RAlt',
    'KEY_LEFTCTRL': 'LCtrl',  'KEY_RIGHTCTRL': 'RCtrl',
    'KEY_LEFTSHIFT': 'LShift','KEY_RIGHTSHIFT': 'RShift',
    'KEY_LEFTMETA': 'LSuper', 'KEY_RIGHTMETA': 'RSuper',
    'KEY_CAPSLOCK': 'CapsLk', 'KEY_SEMICOLON': ';',
    'KEY_APOSTROPHE': "'",    'KEY_GRAVE': '`',
    'KEY_COMMA': ',',         'KEY_DOT': '.',            'KEY_SLASH': '/',
    'KEY_BACKSLASH': '\\',  'KEY_LEFTBRACE': '[',      'KEY_RIGHTBRACE': ']',
    'KEY_MINUS': '-',         'KEY_EQUAL': '=',          'KEY_KPASTERISK': '*',
    **{f'KEY_F{n}': f'F{n}' for n in range(1, 13)},
}


def evdev_to_display(evdev_name):
    """Return human-readable label for an evdev key name.
    Single letters -> lowercase char, numbers -> digit, rest -> DISPLAY_NAMES or short name.
    Handles SHIFT+ prefix.
    """
    if not evdev_name:
        return '?'
    shift = evdev_name.startswith('SHIFT+')
    base = evdev_name[6:] if shift else evdev_name
    if base in DISPLAY_NAMES:
        label = DISPLAY_NAMES[base]
    elif base.startswith('KEY_') and len(base) == 5:
        label = base[4].lower()  # KEY_A -> 'a'
    elif base.startswith('KEY_') and base[4:].isdigit():
        label = base[4:]         # KEY_1 -> '1'
    else:
        label = base[4:].lower() if base.startswith('KEY_') else base  # KEY_COMMA -> 'comma'
    return f'Shift+{label}' if shift else label


def hw_to_evdev(hw_keycode):
    """X11 hardware_keycode -> evdev KEY_* name (no shift handling — raw key only)"""
    return EVDEV_TO_NAME.get(hw_keycode - 8)


def evdev_to_target(evdev_name):
    """Convert evdev key name to JSON-friendly target string.
    Single letters/digits -> lowercase char.
    Punctuation keys with printable chars -> that char.
    Everything else -> evdev name (e.g. KEY_F1).
    """
    _PRINTABLE = {
        'KEY_SEMICOLON':  ';',
        'KEY_APOSTROPHE': "'",
        'KEY_GRAVE':      '`',
        'KEY_COMMA':      ',',
        'KEY_DOT':        '.',
        'KEY_SLASH':      '/',
        'KEY_BACKSLASH':  '\\',
        'KEY_LEFTBRACE':  '[',
        'KEY_RIGHTBRACE': ']',
        'KEY_MINUS':      '-',
        'KEY_EQUAL':      '=',
        'KEY_SPACE':      ' ',
    }
    if not evdev_name:
        return evdev_name
    # Handle SHIFT+ prefix — keep as-is, daemon handles it
    if evdev_name.startswith('SHIFT+'):
        return evdev_name
    if evdev_name in _PRINTABLE:
        return _PRINTABLE[evdev_name]
    if evdev_name.startswith('KEY_') and len(evdev_name) == 5:
        return evdev_name[4].lower()  # KEY_A -> 'a', KEY_1 -> '1'
    return evdev_name


def _any_capture_active(btns):
    """Return True if any KeyCaptureButton in the list is mid-capture."""
    return any(btn._handler_id is not None for btn in btns)

class KeyCaptureButton(Gtk.Button):
    """
    Button that captures the next keypress when clicked.
    Emits 'key-captured' signal with the captured evdev name (str) when done.
    allow_trigger_keys: if True, accepts bare modifier/trigger keys (for trigger key field)
    allow_shift: if True, prefixes SHIFT+ for shifted keys (for compose/target)
    """

    __gsignals__ = {
        # Emitted when capture completes or is aborted.
        # arg: evdev name (e.g. 'KEY_A', 'SHIFT+KEY_A') or '' if aborted via ESC.
        'key-captured': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, label="Click to capture", allow_trigger_keys=False, allow_shift=True):
        super().__init__(label=label)
        self.allow_trigger_keys = allow_trigger_keys
        self.allow_shift     = allow_shift
        self._dialog         = None
        self._handler_id     = None
        self.evdev           = None
        self._prev_evdev     = None
        self._prev_label     = label
        self.connect('clicked', self._on_clicked)

    def _on_clicked(self, _):
        self._prev_evdev = self.evdev
        self._prev_label = self.get_label()
        self.set_label("[ press a key… ]")
        self.set_sensitive(False)
        self._dialog = self.get_toplevel()
        self._handler_id = self._dialog.connect('key-press-event', self._on_key)

    def _on_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            # Abort — restore previous state
            self.evdev = self._prev_evdev
            self.set_label(self._prev_label)
            self._stop_capture()
            self.emit('key-captured', '')
            return True

        hw = event.hardware_keycode
        evdev = hw_to_evdev(hw)

        if not self.allow_trigger_keys and evdev in MODIFIER_EVDEV:
            return True  # ignore pure modifier press

        if evdev:
            if self.allow_shift:
                shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
                if shift and not self.allow_trigger_keys:
                    evdev = f'SHIFT+{evdev}'
            self.evdev = evdev
            self.set_label(evdev_to_display(evdev))
        else:
            self.evdev = None
            self.set_label(f"? (hw={hw})")

        self._stop_capture()
        self.emit('key-captured', self.evdev or '')
        return True

    def _stop_capture(self):
        self.set_sensitive(True)
        if self._handler_id and self._dialog:
            self._dialog.disconnect(self._handler_id)
            self._handler_id = None
            self._dialog = None

    def reset(self, placeholder="Click to capture"):
        self._stop_capture()
        self.evdev = None
        self.set_label(placeholder)


class SequenceTester:
    """
    In-process sequence state machine for the test drawer.
    Mirrors daemon logic but operates on dirty seq_config + live settings.
    No evdev, no uinput — returns human-readable result dicts.
    """

    # Key codes we track as modifiers (same set daemon uses)
    _SHIFT_KEYS  = {42, 54}   # KEY_LEFTSHIFT, KEY_RIGHTSHIFT
    _CTRL_KEYS   = {29, 97}   # KEY_LEFTCTRL, KEY_RIGHTCTRL
    _META_KEYS   = {125, 126} # KEY_LEFTMETA, KEY_RIGHTMETA
    _ALT_KEYS    = {56, 100}  # KEY_LEFTALT, KEY_RIGHTALT
    _ALL_MODS    = _SHIFT_KEYS | _CTRL_KEYS | _META_KEYS | _ALT_KEYS

    def __init__(self):
        self._sequences   = {}        # parsed lookup table: tuple -> output_str
        self._trigger_codes = set()   # int key codes
        self._passthrough_codes = set()
        self._valid_compose = set()   # compose key codes that have sequences
        self._state       = 'IDLE'
        self._trigger     = None
        self._compose     = None
        self._compose_shifted = False
        self._pressed     = set()

    # ── Setup ─────────────────────────────────────────────────────────────

    def load(self, seq_config: dict, settings: dict):
        """Parse dirty seq_config + settings into lookup table."""
        import evdev.ecodes as ec

        self._sequences.clear()
        self._trigger_codes.clear()
        self._passthrough_codes.clear()
        self._valid_compose.clear()
        self._reset_state()

        # Trigger keys
        trigger_defs = settings.get('trigger_key', ['KEY_LEFTALT', 'KEY_RIGHTALT'])
        if isinstance(trigger_defs, str):
            trigger_defs = [trigger_defs]
        for tk in trigger_defs:
            try:
                self._trigger_codes.add(getattr(ec, tk))
            except AttributeError:
                pass

        # Passthrough keys
        for pk in settings.get('passthrough_keys', []):
            try:
                self._passthrough_codes.add(getattr(ec, pk))
            except AttributeError:
                pass

        # Parse sequences: {compose_str: {target_str: output_str}}
        sequences_raw = seq_config.get('sequences', {})
        for compose_str, targets in sequences_raw.items():
            if not isinstance(targets, dict):
                continue
            # Parse compose key (may be SHIFT+key)
            compose_shifted = False
            c_key = compose_str
            if compose_str.upper().startswith('SHIFT+'):
                compose_shifted = True
                c_key = compose_str[6:]
            try:
                compose_code = self._parse_key(c_key)
            except Exception:
                continue

            for target_str, output in targets.items():
                if not isinstance(output, str):
                    continue
                try:
                    target_codes = self._parse_target(target_str)
                except Exception:
                    continue
                for trigger_code in self._trigger_codes:
                    lookup = (trigger_code, compose_shifted, compose_code, *target_codes)
                    self._sequences[lookup] = output
                    self._valid_compose.add(compose_code)

    def _parse_key(self, s: str) -> int:
        import evdev.ecodes as ec
        s = s.strip()
        if s.upper().startswith('KEY_'):
            return getattr(ec, s.upper())
        # single char
        c = s.lower()
        name = f'KEY_{c.upper()}'
        if hasattr(ec, name):
            return getattr(ec, name)
        # punctuation map (same as daemon)
        pmap = {';': 'KEY_SEMICOLON', "'": 'KEY_APOSTROPHE', '`': 'KEY_GRAVE',
                ',': 'KEY_COMMA', '.': 'KEY_DOT', '/': 'KEY_SLASH',
                '\\': 'KEY_BACKSLASH', '-': 'KEY_MINUS', '=': 'KEY_EQUAL',
                '[': 'KEY_LEFTBRACE', ']': 'KEY_RIGHTBRACE'}
        if s in pmap:
            return getattr(ec, pmap[s])
        raise ValueError(f"Unknown key: {s!r}")

    def _parse_target(self, s: str) -> list:
        import evdev.ecodes as ec
        s = s.strip()
        # CTRL+x form
        if '+' in s:
            parts = s.split('+')
            codes = []
            for p in parts:
                p = p.strip()
                if p.upper() in ('CTRL', 'CONTROL'):
                    codes.append(ec.KEY_LEFTCTRL)
                elif p.upper() == 'SHIFT':
                    codes.append(ec.KEY_LEFTSHIFT)
                elif p.upper() == 'ALT':
                    codes.append(ec.KEY_LEFTALT)
                else:
                    codes.append(self._parse_key(p))
            return codes
        # uppercase single char → add shift
        if len(s) == 1 and s.isupper():
            return [ec.KEY_LEFTSHIFT, self._parse_key(s)]
        return [self._parse_key(s)]

    # ── State machine ──────────────────────────────────────────────────────

    def _reset_state(self):
        self._state   = 'IDLE'
        self._trigger = None
        self._compose = None
        self._compose_shifted = False

    def feed_key(self, key_code: int, value: int) -> dict | None:
        import evdev.ecodes as ec

        if value == 1:
            self._pressed.add(key_code)
        elif value == 0:
            self._pressed.discard(key_code)

        # Repeats ignored during compose (same as daemon)
        if value == 2 and self._state != 'IDLE':
            return None

        # ESC always cancels
        if key_code == ec.KEY_ESC and value == 1:
            if self._state != 'IDLE':
                self._reset_state()
                return None
            return None

        if self._state == 'IDLE':
            if value != 1:
                return None
            if key_code in self._trigger_codes:
                other_mods = self._CTRL_KEYS | self._META_KEYS
                if self._pressed & other_mods - {key_code}:
                    return {'status': 'passthrough', 'trigger': None, 'compose': None,
                            'target': None, 'output': None,
                            'reason': 'Trigger with Ctrl/Meta — regular shortcut'}
                self._trigger = key_code
                self._state   = 'TRIGGER_PRESSED'
            return None

        elif self._state == 'TRIGGER_PRESSED':
            if value == 0 and key_code == self._trigger:
                self._reset_state()
                return {'status': 'passthrough', 'trigger': self._fmt(self._trigger),
                        'compose': None, 'target': None, 'output': None,
                        'reason': 'Trigger released alone'}
            if value == 1:
                if key_code in (self._SHIFT_KEYS):
                    return None  # wait to see compose key
                if key_code in self._passthrough_codes:
                    res = {'status': 'passthrough',
                           'trigger': self._fmt(self._trigger),
                           'compose': self._fmt(key_code),
                           'target': None, 'output': None,
                           'reason': f'{self._fmt(key_code)} is in passthrough list'}
                    self._reset_state()
                    return res
                if key_code in self._CTRL_KEYS | self._META_KEYS:
                    self._reset_state()
                    return {'status': 'passthrough', 'trigger': self._fmt(self._trigger),
                            'compose': self._fmt(key_code), 'target': None, 'output': None,
                            'reason': 'Additional modifier — regular shortcut'}
                if key_code not in self._valid_compose:
                    res = {'status': 'passthrough',
                           'trigger': self._fmt(self._trigger),
                           'compose': self._fmt(key_code),
                           'target': None, 'output': None,
                           'reason': f'{self._fmt(key_code)} has no sequences defined'}
                    self._reset_state()
                    return res
                self._compose = key_code
                self._compose_shifted = bool(self._pressed & self._SHIFT_KEYS)
                self._state = 'COMPOSE_PRESSED'
            return None

        elif self._state == 'COMPOSE_PRESSED':
            # Allow trigger key releases through — needed for transition to WAITING_TARGET
            if key_code in self._ALL_MODS and key_code not in self._trigger_codes:
                return None
            if value == 0 and key_code in (self._trigger, self._compose):
                t_gone = self._trigger not in self._pressed
                c_gone = self._compose not in self._pressed
                if t_gone and c_gone:
                    self._state = 'WAITING_TARGET'
                    return {'status': 'waiting',
                            'trigger': self._fmt(self._trigger),
                            'compose': ('Shift+' if self._compose_shifted else '') + self._fmt(self._compose),
                            'target': None, 'output': None, 'reason': None}
            return None

        elif self._state == 'WAITING_TARGET':
            if key_code in self._ALL_MODS:
                return None
            if value != 1:
                return None

            target_shifted = bool(self._pressed & self._SHIFT_KEYS)
            target_codes   = []
            if target_shifted:
                import evdev.ecodes as ec2
                target_codes.append(ec2.KEY_LEFTSHIFT)
            target_codes.append(key_code)

            lookup = (self._trigger, self._compose_shifted, self._compose, *target_codes)

            matched_output = self._sequences.get(lookup)
            if matched_output is None and target_shifted:
                # Try unshifted fallback
                import evdev.ecodes as ec2
                ul = (self._trigger, self._compose_shifted, self._compose, key_code)
                matched_output = self._sequences.get(ul)

            trigger_disp = self._fmt(self._trigger)
            compose_disp = ('Shift+' if self._compose_shifted else '') + self._fmt(self._compose)
            target_disp  = ('Shift+' if target_shifted else '') + self._fmt(key_code)

            self._reset_state()

            if matched_output is not None:
                return {'status': 'matched', 'trigger': trigger_disp,
                        'compose': compose_disp, 'target': target_disp,
                        'output': matched_output, 'reason': None}
            else:
                return {'status': 'no_match', 'trigger': trigger_disp,
                        'compose': compose_disp, 'target': target_disp,
                        'output': None, 'reason': 'No sequence matched — passed through'}

        return None

    def _fmt(self, code: int) -> str:
        """Format a key code as a human-readable string."""
        if code is None:
            return '—'
        try:
            import evdev.ecodes as ec
            name = ec.KEY.get(code, f'KEY_{code}')
            # Strip KEY_ prefix and title-case
            if name.startswith('KEY_'):
                return name[4:].title()
            return name
        except Exception:
            return str(code)

    def in_waiting_state(self) -> bool:
        return self._state == 'WAITING_TARGET'

    def reset(self):
        self._reset_state()
        self._pressed.clear()


class SequenceEditorDialog(Gtk.Window):

    def __init__(self, parent, sequence_config_path, on_saved=None):
        super().__init__(title="Edit Sequence", transient_for=parent, modal=True)
        self.set_default_size(620, 580)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_border_width(0)
        self._saved_callback = on_saved or (lambda: None)
        self.connect('delete-event', self._on_delete_event)
        self.connect('key-press-event', self._on_key_press)
        self.connect('destroy', lambda _: TEST_MODE_FILE.unlink(missing_ok=True))

        self.seq_path   = Path(sequence_config_path) if sequence_config_path else None
        self.seq_config = self._load_json(self.seq_path, {"sequences": {}}) if self.seq_path else {"sequences": {}}
        self.settings   = self._load_json(SETTINGS_PATH, {})

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)
        outer.pack_start(box, True, True, 0)

        # ── Bottom button row ─────────────────────────────────────────────
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_row.set_border_width(6)
        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect('clicked', lambda _: self.destroy())
        self.btn_save = Gtk.Button(label="Save")
        self.btn_save.connect('clicked', self._on_save_clicked)
        btn_row.pack_end(self.btn_save, False, False, 0)
        btn_row.pack_end(btn_cancel, False, False, 0)

        self.btn_test = Gtk.ToggleButton(label="▶ Test")
        self.btn_test.connect('toggled', self._on_test_toggled)
        btn_row.pack_start(self.btn_test, False, False, 0)

        outer.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        # ── Test drawer (hidden until Test button toggled) ─────────────────
        self._tester      = SequenceTester()
        self._test_active = False

        self._test_revealer = Gtk.Revealer()
        self._test_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._test_revealer.set_transition_duration(200)

        test_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        test_box.set_border_width(10)

        test_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)

        title_lbl = Gtk.Label(xalign=0)
        title_lbl.set_markup('<b>Test sequences</b>  <small>(type a sequence — results appear below)</small>')
        test_box.pack_start(title_lbl, False, False, 0)

        capture_lbl = Gtk.Label(xalign=0)
        capture_lbl.set_markup('<small>🔒 Keyboard captured while test is active — Alt-Tab, Ctrl-V etc. will not work</small>')
        capture_lbl.get_style_context().add_class('dim-label')
        test_box.pack_start(capture_lbl, False, False, 0)

        # Result grid
        result_grid = Gtk.Grid(column_spacing=10, row_spacing=4)

        def _ro_entry():
            e = Gtk.Entry()
            e.set_editable(False)
            e.set_can_focus(False)
            e.set_width_chars(14)
            return e

        result_grid.attach(Gtk.Label(label="Trigger:", xalign=1),  0, 0, 1, 1)
        self._t_trigger = _ro_entry()
        result_grid.attach(self._t_trigger, 1, 0, 1, 1)

        result_grid.attach(Gtk.Label(label="Compose:", xalign=1), 0, 1, 1, 1)
        self._t_compose = _ro_entry()
        result_grid.attach(self._t_compose, 1, 1, 1, 1)

        result_grid.attach(Gtk.Label(label="Target:", xalign=1),  0, 2, 1, 1)
        self._t_target  = _ro_entry()
        result_grid.attach(self._t_target,  1, 2, 1, 1)

        result_grid.attach(Gtk.Label(label="Output:", xalign=1),  0, 3, 1, 1)
        self._t_output  = _ro_entry()
        self._t_output.set_width_chars(24)
        result_grid.attach(self._t_output,  1, 3, 1, 1)

        test_box.pack_start(result_grid, False, False, 0)

        self._t_status = Gtk.Label(label="", xalign=0)
        self._t_status.set_line_wrap(True)
        sc = self._t_status.get_style_context()
        sc.add_class('dim-label')
        test_box.pack_start(self._t_status, False, False, 0)

        btn_clear_test = Gtk.Button(label="Clear")
        btn_clear_test.set_halign(Gtk.Align.START)
        btn_clear_test.connect('clicked', lambda _: self._test_clear())
        test_box.pack_start(btn_clear_test, False, False, 0)

        self._test_revealer.add(test_box)
        outer.pack_start(self._test_revealer, False, False, 0)

        outer.pack_start(btn_row, False, False, 0)

        # ── Name & Description ────────────────────────────────────────────
        meta_grid = Gtk.Grid()
        meta_grid.set_column_spacing(8)
        meta_grid.set_row_spacing(6)
        box.pack_start(meta_grid, False, False, 0)

        meta_grid.attach(Gtk.Label(label="Name:", xalign=1), 0, 0, 1, 1)
        self.name_entry = Gtk.Entry()
        self.name_entry.set_text(self.seq_config.get('name', ''))
        self.name_entry.set_hexpand(True)
        self.name_entry.connect('changed', self._on_state_changed)
        meta_grid.attach(self.name_entry, 1, 0, 1, 1)

        meta_grid.attach(Gtk.Label(label="Description:", xalign=1), 0, 1, 1, 1)
        self.desc_entry = Gtk.Entry()
        self.desc_entry.set_text(self.seq_config.get('description', ''))
        self.desc_entry.set_hexpand(True)
        self.desc_entry.connect('changed', self._on_state_changed)
        meta_grid.attach(self.desc_entry, 1, 1, 1, 1)

        # ── Sequences ──────────────────────────────────────────────────────
        seq_frame = Gtk.Frame(label=" Sequences ")
        seq_frame.set_label_align(0.02, 0.5)
        seq_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        seq_hbox.set_border_width(8)
        seq_frame.add(seq_hbox)

        # Left: list + Delete button
        list_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        seq_hbox.pack_start(list_vbox, True, True, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(150)

        self.store = Gtk.ListStore(str, str, str, str, str)  # disp_compose, disp_target, output, raw_compose, raw_target
        self.tree  = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)
        for i, title in enumerate(["Compose Key", "Target", "Output"]):
            r   = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, r, text=i)
            col.set_expand(True)
            self.tree.append_column(col)
        self.tree.get_selection().connect('changed', self._on_sel_changed)
        scroll.add(self.tree)
        list_vbox.pack_start(scroll, True, True, 0)

        list_btns = Gtk.Box(spacing=4)
        self.btn_delete = Gtk.Button(label="Delete Selected")
        self.btn_delete.set_sensitive(False)
        self.btn_delete.connect('clicked', self._on_delete)
        list_btns.pack_end(self.btn_delete, False, False, 0)
        list_vbox.pack_start(list_btns, False, False, 0)

        # Right: Add / Edit form
        form_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        form_vbox.set_valign(Gtk.Align.START)
        seq_hbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        seq_hbox.pack_start(form_vbox, False, False, 0)

        form_vbox.pack_start(Gtk.Label(label="Add / Edit Sequence:", xalign=0), False, False, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=6)

        grid.attach(Gtk.Label(label="Compose key:", xalign=0), 0, 0, 1, 1)
        self.btn_compose = KeyCaptureButton(allow_shift=True)
        grid.attach(self.btn_compose, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Target key:", xalign=0), 0, 1, 1, 1)
        self.btn_target = KeyCaptureButton(allow_shift=True)
        grid.attach(self.btn_target, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="Output:", xalign=0), 0, 2, 1, 1)
        self.output_entry = Gtk.Entry()
        self.output_entry.set_placeholder_text("e.g. ä or user@example.com")
        grid.attach(self.output_entry, 1, 2, 1, 1)

        form_vbox.pack_start(grid, False, False, 0)

        self.status_label = Gtk.Label(label="", xalign=0)
        form_vbox.pack_start(self.status_label, False, False, 0)

        btn_row = Gtk.Box(spacing=6)
        self.btn_add = Gtk.Button(label="Add to List")
        self.btn_add.connect('clicked', self._on_add)
        btn_row.pack_start(self.btn_add, False, False, 0)
        btn_clear = Gtk.Button(label="Clear Form")
        btn_clear.connect('clicked', lambda _: self._reset_form())
        btn_row.pack_start(btn_clear, False, False, 0)
        form_vbox.pack_start(btn_row, False, False, 0)

        box.pack_start(seq_frame, True, True, 0)

        self._refresh_list()
        self.show_all()
        self._orig_snapshot = self._snapshot()
        self._saved = False

    # ── Helpers ───────────────────────────────────────────────────────────

    def _load_json(self, path, default):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return default

    def _on_key_press(self, widget, event):
        # ESC is always handled first — closes dialog or stops test
        if event.keyval == Gdk.KEY_Escape and not self._any_capture_active():
            if self._test_active:
                self._stop_test()
            else:
                self.destroy()
            return True
        # Route to tester when drawer is open (and no capture dialog is active)
        if self._test_active and not self._any_capture_active():
            hw = event.hardware_keycode
            import evdev.ecodes as ec
            # hardware_keycode in GTK is evdev code + 8
            evdev_code = hw - 8
            result = self._tester.feed_key(evdev_code, 1)
            if result:
                self._test_show(result)
            elif self._tester.in_waiting_state():
                GLib.timeout_add(self._test_timeout_ms, self._test_timeout_reset)
            return True   # swallow — don't let GTK process it further
        return False

    def _test_timeout_reset(self):
        """Called by GLib timer — reset tester if still waiting for target."""
        if self._test_active and self._tester.in_waiting_state():
            self._tester.reset()
            self._t_status.set_text("⏱ Timed out — sequence cancelled")
        return False  # don't repeat

    def _on_key_release(self, widget, event):
        if self._test_active and not self._any_capture_active():
            evdev_code = event.hardware_keycode - 8
            result = self._tester.feed_key(evdev_code, 0)
            if result:
                self._test_show(result)
            return True
        return False

    def _on_delete_event(self, widget, event):
        """Block close while a key capture is in progress."""
        if self._test_active:
            self._stop_test()
        return self._any_capture_active()

    def _any_capture_active(self):
        return _any_capture_active([self.btn_compose, self.btn_target])

    # ── Test drawer ───────────────────────────────────────────────────────

    def _on_test_toggled(self, btn):
        if btn.get_active():
            self._start_test()
        else:
            self._stop_test()

    def _start_test(self):
        # Load dirty state into tester
        settings = self._load_json(SETTINGS_PATH, {})
        self._tester.load(self.seq_config, settings)
        self._tester.reset()
        self._test_timeout_ms = settings.get('settings', {}).get('timeout_ms', 1000)
        self._test_active = True
        # Write flag file so daemon passes through
        try:
            TEST_MODE_FILE.touch()
        except Exception:
            pass
        # Freeze editor controls
        self._set_editor_sensitive(False)
        # Connect key-release
        self._key_release_handler = self.connect('key-release-event', self._on_key_release)
        self._test_revealer.set_reveal_child(True)
        self._test_clear()
        self._t_status.set_text("🔒 Keyboard captured — type a sequence")
        # Hard X11 keyboard grab so this window receives all key events
        self.present()
        gdkwin = self.get_window()
        if gdkwin:
            result = Gdk.keyboard_grab(gdkwin, True, Gdk.CURRENT_TIME)
            if result != Gdk.GrabStatus.SUCCESS:
                self._t_status.set_text("⚠ Could not grab keyboard — click here first, then type")

    def _stop_test(self):
        self._test_active = False
        # Release X11 keyboard grab
        Gdk.keyboard_ungrab(Gdk.CURRENT_TIME)
        # Remove flag file
        try:
            TEST_MODE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        # Disconnect key-release
        if hasattr(self, '_key_release_handler'):
            try:
                self.disconnect(self._key_release_handler)
            except Exception:
                pass
        self._tester.reset()
        self._set_editor_sensitive(True)
        self._test_revealer.set_reveal_child(False)
        if self.btn_test.get_active():
            self.btn_test.set_active(False)

    def _set_editor_sensitive(self, sensitive: bool):
        for w in (self.btn_compose, self.btn_target, self.output_entry,
                  self.btn_add, self.btn_delete, self.btn_save,
                  self.name_entry, self.desc_entry):
            w.set_sensitive(sensitive)

    def _test_clear(self):
        self._tester.reset()
        for w in (self._t_trigger, self._t_compose, self._t_target, self._t_output):
            w.set_text('')
        self._t_status.set_text("🔒 Keyboard captured — type a sequence")
        # Remove any coloring
        for w in (self._t_trigger, self._t_compose, self._t_target, self._t_output):
            w.get_style_context().remove_class('test-matched')
            w.get_style_context().remove_class('test-nomatch')

    def _test_show(self, result: dict):
        self._t_trigger.set_text(result.get('trigger') or '')
        self._t_compose.set_text(result.get('compose') or '')
        self._t_target.set_text( result.get('target')  or '')
        self._t_output.set_text( result.get('output')  or '')

        status = result.get('status')
        reason = result.get('reason') or ''

        css_matched  = 'test-matched'
        css_nomatch  = 'test-nomatch'

        for w in (self._t_trigger, self._t_compose, self._t_target, self._t_output):
            sc = w.get_style_context()
            sc.remove_class(css_matched)
            sc.remove_class(css_nomatch)

        if status == 'waiting':
            self._t_status.set_text("⌨ Waiting for target key…")
        elif status == 'matched':
            for w in (self._t_output,):
                w.get_style_context().add_class(css_matched)
            self._t_status.set_text(f"✓ Matched → {result.get('output')}")
        elif status == 'no_match':
            for w in (self._t_target,):
                w.get_style_context().add_class(css_nomatch)
            self._t_status.set_text(f"↷ Passed through — {reason}")
        elif status == 'passthrough':
            self._t_status.set_text(f"↷ Passed through — {reason}")
        elif status == 'ignored':
            self._t_status.set_text(f"✗ Ignored — {reason}")

    # ── Sequences ─────────────────────────────────────────────────────────

    def _refresh_list(self):
        self.store.clear()
        for compose_key, targets in self.seq_config.get('sequences', {}).items():
            if isinstance(targets, dict):
                for target, output in targets.items():
                    self.store.append([evdev_to_display(compose_key),
                                       evdev_to_display(target),
                                       str(output),
                                       compose_key, target])
            elif isinstance(targets, str):
                self.store.append([evdev_to_display(compose_key), '→ alias', targets,
                                   compose_key, ''])

    def _on_sel_changed(self, selection):
        model, it = selection.get_selected()
        self.btn_delete.set_sensitive(it is not None)
        if it:
            self.btn_compose.evdev = model[it][3]
            self.btn_compose.set_label(model[it][0])
            self.btn_target.evdev = model[it][4]
            self.btn_target.set_label(model[it][1])
            self.output_entry.set_text(model[it][2])
            self.btn_add.set_label("Save")
            self._set_status("Editing selected row — update values and click Save")
        else:
            self.btn_add.set_label("Add to List")

    def _on_delete(self, _):
        _, it = self.tree.get_selection().get_selected()
        if not it:
            return
        compose_key = self.store[it][3]
        target      = self.store[it][4]
        seqs = self.seq_config.setdefault('sequences', {})
        if compose_key in seqs and isinstance(seqs[compose_key], dict):
            seqs[compose_key].pop(target, None)
            if not seqs[compose_key]:
                del seqs[compose_key]
        self._refresh_list()
        self._reset_form()
        self._set_status("Deleted.")
        self._on_state_changed()

    def _on_add(self, _):
        compose = self.btn_compose.evdev
        target  = self.btn_target.evdev
        output  = self.output_entry.get_text().strip()

        if not compose:
            self._set_status("⚠ Capture a compose key first")
            return
        if not target:
            self._set_status("⚠ Capture a target key first")
            return
        if not output:
            self._set_status("⚠ Enter an output value")
            return

        # If editing an existing row, remove the old entry first
        _, it = self.tree.get_selection().get_selected()
        if it:
            old_compose = self.store[it][3]
            old_target  = self.store[it][4]
            seqs = self.seq_config.setdefault('sequences', {})
            if old_compose in seqs and isinstance(seqs[old_compose], dict):
                seqs[old_compose].pop(old_target, None)
                if not seqs[old_compose]:
                    del seqs[old_compose]

        target_cfg = evdev_to_target(target)
        self.seq_config.setdefault('sequences', {}).setdefault(compose, {})[target_cfg] = output
        self._refresh_list()
        self._set_status(f"✓ Added: {compose} + {target_cfg} → {output}")
        self._reset_form()
        self._on_state_changed()

    def _reset_form(self):
        self.btn_compose.reset()
        self.btn_target.reset()
        self.output_entry.set_text("")
        self.status_label.set_text("")
        self.btn_add.set_label("Add to List")
        self.tree.get_selection().unselect_all()

    def _set_status(self, msg, transient=True):
        self.status_label.set_text(msg)
        if transient and msg:
            GLib.timeout_add_seconds(3, lambda: self.get_realized() and
                                     (self.status_label.set_text('') or False))

    def _snapshot(self):
        return json.dumps({
            'name': self.name_entry.get_text(),
            'description': self.desc_entry.get_text(),
            'sequences': self.seq_config.get('sequences', {})
        }, sort_keys=True)

    def _on_save_clicked(self, _):
        ok, err = self.save()
        if ok:
            self._saved = True
            self._saved_callback()
            self.destroy()
        else:
            from gi.repository import Gtk as _Gtk
            dlg = _Gtk.MessageDialog(transient_for=self, modal=True,
                message_type=_Gtk.MessageType.ERROR,
                buttons=_Gtk.ButtonsType.OK, text=err or "Failed to save")
            dlg.run()
            dlg.destroy()

    def _on_state_changed(self, *_):
        self.btn_save.set_sensitive(self._snapshot() != self._orig_snapshot)

    def save(self):
        name = self.name_entry.get_text().strip()
        if not name:
            return False, "Name cannot be empty"
        self.seq_config['name'] = name
        self.seq_config['description'] = self.desc_entry.get_text().strip()

        slug = re.sub(r'[^a-zA-Z0-9]+', '-', name).strip('-').lower()
        if not slug:
            return False, "Name produces an empty filename"

        parent = self.seq_path.parent if self.seq_path else USER_CONFIG_DIR
        new_path = parent / f"{slug}.config.json"

        # Avoid overwriting a different file
        if new_path.exists() and new_path != self.seq_path:
            return False, f"A config named '{slug}.config.json' already exists.\nChoose a different name."

        try:
            with open(new_path, 'w') as f:
                json.dump(self.seq_config, f, indent=2, ensure_ascii=False)
            # Rename old file if path changed
            if self.seq_path and self.seq_path.exists() and new_path != self.seq_path:
                self.seq_path.unlink()
            self.seq_path = new_path
        except Exception as e:
            return False, f"Failed to save: {e}"
        return True, None

    def get_sequences(self):
        return self.seq_config.get('sequences', {})



LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR']
TIMEOUT_MIN = 200
TIMEOUT_MAX = 1500


def get_icon_sets():
    """Return dict of icon set name -> {active: path, inactive: path, error: path}"""
    sets = {}
    for d in [SYSTEM_ICON_DIR, USER_ICON_DIR]:
        if not d.exists():
            continue
        for p in sorted(d.glob('*.active.png')):
            name = p.stem.replace('.active', '')
            inactive = p.parent / f'{name}.inactive.png'
            error    = p.parent / f'{name}.error.png'
            sets[name] = {
                'active':   str(p),
                'inactive': str(inactive) if inactive.exists() else str(p),
                'error':    str(error) if error.exists() else '',
            }
    return sets



class ConfigManager(Gtk.Window):

    def __init__(self):
        super().__init__(title="Umlaut Config Manager")
        self.set_default_size(660, 280)
        self.set_border_width(8)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect('key-press-event', self._on_key_press)
        self.present()

        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.enabled_configs = []
        self._load_enabled()

        overrides = load_settings()
        self._settings_valid = SETTINGS_PATH.exists() and bool(overrides)
        settings = overrides.get('settings', {})
        self._pending_timeout = settings.get('timeout_ms', 1000)
        self._pending_loglevel = settings.get('log_level', 'INFO')
        self._pending_passthrough_keys = list(overrides.get('passthrough_keys', []))
        trigger = overrides.get('trigger_key', ['KEY_LEFTALT', 'KEY_RIGHTALT'])
        if isinstance(trigger, str):
            trigger = [trigger]
        self._pending_trigger_keys = list(trigger)

        self._build_ui()
        self._refresh_list()
        # Defer warning display until window is visible
        if not SETTINGS_PATH.exists():
            GLib.idle_add(lambda: self._set_status(
                "⚠ Settings file missing — showing defaults. Click 'Create Settings' to save.", transient=False) or False)

        # Dirty tracking — snapshot disk state, wire up all widgets
        self._disk_snapshot = self._snapshot(from_disk=True)
        self.timeout_spin.connect('value-changed', self._check_dirty)
        self.ll_combo.connect('changed', self._check_dirty)
        self.icon_combo.connect('changed', self._check_dirty)
        self.trigger_both_check.connect('toggled', self._check_dirty)
        self.btn_trigger.connect('key-captured', self._check_dirty)
        self._passthrough_capture_btn.connect('key-captured', self._check_dirty)

    def _on_key_press(self, widget, event):
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if event.keyval == Gdk.KEY_Escape:
            if self._any_capture_active():
                return False
            self.destroy()
            return True
        if ctrl and event.keyval == Gdk.KEY_Tab:
            n = self.notebook.get_n_pages()
            self.notebook.set_current_page((self.notebook.get_current_page() + 1) % n)
            return True
        if ctrl and event.keyval == Gdk.KEY_ISO_Left_Tab:  # Shift+Ctrl+Tab
            n = self.notebook.get_n_pages()
            self.notebook.set_current_page((self.notebook.get_current_page() - 1) % n)
            return True
        return False

    def _any_capture_active(self):
        return _any_capture_active([self.btn_trigger, self._passthrough_capture_btn])

    def _set_status(self, msg, transient=True):
        self.status_label.set_text(msg)
        if transient and msg:
            GLib.timeout_add_seconds(3, lambda: self.get_realized() and
                                     (self.status_label.set_text('') or False))

    def _snapshot(self, from_disk=False):
        """Snapshot settings for dirty-checking.
        source='disk' reads saved state; source='ui' reads current widget state.
        """
        if from_disk:
            s = load_settings()
            return json.dumps({
                'enabled_sequences': sorted(self.enabled_configs),
                'timeout_ms':       s.get('settings', {}).get('timeout_ms', 1000),
                'log_level':        s.get('settings', {}).get('log_level', 'INFO'),
                'passthrough_keys': s.get('passthrough_keys', []),
                'trigger_key':      s.get('trigger_key', []),
                'icon_set':         s.get('icon_set', 'default'),
            }, sort_keys=True)
        else:
            return json.dumps({
                'enabled_sequences': sorted(self.enabled_configs),
                'timeout_ms':       int(self.timeout_spin.get_value()),
                'log_level':        self.ll_combo.get_active_text() or '',
                'passthrough_keys': self._pending_passthrough_keys,
                'trigger_key':      self._get_trigger_key_list(),
                'icon_set':         self.icon_combo.get_active_text() or '',
            }, sort_keys=True)

    def _on_tab_switched(self, notebook, page, page_num):
        on_settings = (page_num == 1)
        self.btn_apply.set_visible(on_settings)
        if on_settings:
            if not SETTINGS_PATH.exists():
                self._set_status("⚠ Settings file missing — showing defaults. Click 'Create Settings' to save.", transient=False)
            self._check_dirty()

    def _check_dirty(self, *_):
        if not self._settings_valid:
            self.btn_apply.set_label("Create Settings")
            self.btn_apply.set_sensitive(True)
            return
        if self.btn_apply.get_label() == "Create Settings":
            self.btn_apply.set_label("Apply Changes")
        self.btn_apply.set_sensitive(self._snapshot() != self._disk_snapshot)

    def _load_enabled(self):
        self.enabled_configs = list(load_settings().get('enabled_sequences', []))

    def _save_enabled(self):
        try:
            s = load_settings()
            s['enabled_sequences'] = self.enabled_configs
            save_settings(s)
            return True
        except Exception as e:
            self._error(f"Failed to save enabled_sequences: {e}")
            return False

    def _build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add(outer)
        self.notebook = Gtk.Notebook()
        outer.pack_start(self.notebook, True, True, 0)
        self.notebook.append_page(self._build_sequences_tab(), Gtk.Label(label="Sequences"))
        self.notebook.append_page(self._build_settings_tab(), Gtk.Label(label="Settings"))
        self.notebook.connect('switch-page', self._on_tab_switched)
        outer.pack_start(self._build_button_row(), False, False, 0)

    def _build_sequences_tab(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_border_width(8)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        vbox.pack_start(hbox, True, True, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self.store = Gtk.ListStore(bool, str, str, str, str)  # enabled, name, desc, filename, type
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)

        toggle_r = Gtk.CellRendererToggle()
        toggle_r.connect("toggled", self._on_toggled)
        self.col_en = Gtk.TreeViewColumn("Enabled", toggle_r, active=0)
        self.col_en.set_min_width(60)
        self.tree.append_column(self.col_en)

        col_name = Gtk.TreeViewColumn("Config")
        col_name.set_expand(True)
        name_r = Gtk.CellRendererText()
        col_name.pack_start(name_r, True)
        col_name.add_attribute(name_r, 'text', 1)
        desc_r = Gtk.CellRendererText()
        desc_r.set_property('foreground', '#888888')
        desc_r.set_property('scale', 0.85)
        col_name.pack_start(desc_r, True)
        col_name.add_attribute(desc_r, 'text', 2)
        self.tree.append_column(col_name)

        scroll.add(self.tree)
        self.tree.connect('row-activated', self._on_row_activated)
        hbox.pack_start(scroll, True, True, 0)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        btn_box.set_valign(Gtk.Align.START)
        hbox.pack_start(btn_box, False, False, 0)

        for label, handler in [("New", self._on_new),
                                ("Edit", self._on_edit),
                                ("Delete", self._on_delete)]:
            btn = Gtk.Button(label=label)
            btn.set_size_request(100, -1)
            btn.connect("clicked", handler)
            btn_box.pack_start(btn, False, False, 0)

        btn_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        for label, handler in [("▲ Up", self._on_move_up),
                                ("▼ Down", self._on_move_down)]:
            btn = Gtk.Button(label=label)
            btn.set_size_request(100, -1)
            btn.connect("clicked", handler)
            btn_box.pack_start(btn, False, False, 0)

        return vbox

    def _build_settings_tab(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_border_width(12)

        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(8)
        vbox.pack_start(grid, False, False, 0)

        # Trigger key
        grid.attach(Gtk.Label(label="Trigger key:", xalign=1), 0, 0, 1, 1)
        trigger_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        initial_trigger = self._pending_trigger_keys[0] if self._pending_trigger_keys else 'KEY_LEFTALT'
        self.btn_trigger = KeyCaptureButton(label=evdev_to_display(initial_trigger),
                                            allow_trigger_keys=True, allow_shift=False)
        self.btn_trigger.evdev = initial_trigger
        self.btn_trigger.connect('key-captured', self._on_trigger_captured)
        trigger_box.pack_start(self.btn_trigger, False, False, 0)
        self.trigger_both_check = Gtk.CheckButton(label="Both (L + R)")
        self.trigger_both_check.set_tooltip_text("Only available for trigger keys (Alt, Ctrl, Shift, Super)")
        if initial_trigger in BOTH_PAIRS:
            self.trigger_both_check.set_active(len(self._pending_trigger_keys) >= 2)
            self.trigger_both_check.set_sensitive(True)
        else:
            self.trigger_both_check.set_active(False)
            self.trigger_both_check.set_sensitive(False)
        trigger_box.pack_start(self.trigger_both_check, False, False, 0)
        grid.attach(trigger_box, 1, 0, 1, 1)

        # Timeout
        grid.attach(Gtk.Label(label="Timeout (ms):", xalign=1), 0, 1, 1, 1)
        adj = Gtk.Adjustment(value=self._pending_timeout,
                             lower=TIMEOUT_MIN, upper=TIMEOUT_MAX,
                             step_increment=50, page_increment=100)
        self.timeout_spin = Gtk.SpinButton(adjustment=adj, climb_rate=1, digits=0)
        self.timeout_spin.set_numeric(True)
        self.timeout_spin.set_width_chars(5)
        grid.attach(self.timeout_spin, 1, 1, 1, 1)

        # Log level
        grid.attach(Gtk.Label(label="Log level:", xalign=1), 0, 2, 1, 1)
        self.ll_combo = Gtk.ComboBoxText()
        for lvl in LOG_LEVELS:
            self.ll_combo.append_text(lvl)
        idx = LOG_LEVELS.index(self._pending_loglevel) \
            if self._pending_loglevel in LOG_LEVELS else 1
        self.ll_combo.set_active(idx)
        grid.attach(self.ll_combo, 1, 2, 1, 1)

        self._build_passthrough_keys(vbox)
        self._build_icon_picker(vbox)
        return vbox

    def _build_button_row(self):
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_row.set_border_width(6)
        self.status_label = Gtk.Label(label="", xalign=0)
        self.status_label.set_hexpand(True)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        btn_row.pack_start(self.status_label, True, True, 0)
        apply_label = "Create Settings" if not self._settings_valid else "Apply Changes"
        self.btn_apply = Gtk.Button(label=apply_label)
        self.btn_apply.connect("clicked", self._on_apply)
        self.btn_apply.set_sensitive(not self._settings_valid)
        self.btn_apply.set_no_show_all(True)
        self.btn_apply.set_visible(False)  # hidden until Settings tab
        btn_row.pack_end(self.btn_apply, False, False, 0)
        btn_close = Gtk.Button(label="Close")
        btn_close.connect("clicked", lambda _: self.destroy())
        btn_row.pack_end(btn_close, False, False, 0)
        return btn_row

    def _on_trigger_captured(self, btn, evdev):
        if evdev:
            has_pair = evdev in BOTH_PAIRS
            self.trigger_both_check.set_sensitive(has_pair)
            if not has_pair:
                self.trigger_both_check.set_active(False)
        self._check_dirty()

    def _get_trigger_key_list(self):
        evdev = self.btn_trigger.evdev
        if not evdev:
            return self._pending_trigger_keys
        if self.trigger_both_check.get_sensitive() and self.trigger_both_check.get_active():
            pair = BOTH_PAIRS.get(evdev)
            return [evdev, pair] if pair else [evdev]
        return [evdev]

    def _build_passthrough_keys(self, vbox):
        """Build pass-through keys listbox with Add/Remove buttons."""

        # Frame with tooltip on hover
        frame = Gtk.Frame(label="Pass-through keys")
        frame.set_border_width(4)
        frame.set_tooltip_text(
            "Pass-through keys are passed through to the system even if a "
            "sequence defines them as compose keys (e.g. Tab for Alt+Tab)."
        )
        vbox.pack_start(frame, False, False, 0)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hbox.set_border_width(4)
        frame.add(hbox)

        # Listbox showing current ignore keys
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(-1, 70)
        self.passthrough_store = Gtk.ListStore(str, str)  # evdev_name, display_name
        self.passthrough_tree = Gtk.TreeView(model=self.passthrough_store)
        self.passthrough_tree.set_headers_visible(False)
        col = Gtk.TreeViewColumn("Key", Gtk.CellRendererText(), text=1)
        self.passthrough_tree.append_column(col)
        scroll.add(self.passthrough_tree)
        hbox.pack_start(scroll, True, True, 0)

        # Populate from pending state
        for k in self._pending_passthrough_keys:
            self.passthrough_store.append([k, evdev_to_display(k)])

        # Add / Remove buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        btn_box.set_valign(Gtk.Align.CENTER)
        hbox.pack_start(btn_box, False, False, 0)

        self._passthrough_capture_btn = KeyCaptureButton(
            label="Add key…", allow_trigger_keys=False, allow_shift=False)
        self._passthrough_capture_btn.connect('key-captured', self._on_passthrough_captured)
        btn_box.pack_start(self._passthrough_capture_btn, False, False, 0)

        btn_remove = Gtk.Button(label="Remove")
        btn_remove.connect('clicked', self._on_passthrough_remove)
        btn_box.pack_start(btn_remove, False, False, 0)

    def _on_passthrough_captured(self, btn, evdev):
        if evdev and evdev not in self._pending_passthrough_keys:
            self._pending_passthrough_keys.append(evdev)
            self.passthrough_store.append([evdev, evdev_to_display(evdev)])
            self._check_dirty()
        btn.reset("Add key…")

    def _on_passthrough_remove(self, _):
        model, it = self.passthrough_tree.get_selection().get_selected()
        if not it:
            return
        evdev = model[it][0]
        self._pending_passthrough_keys = [k for k in self._pending_passthrough_keys if k != evdev]
        model.remove(it)
        self._check_dirty()

    def _build_icon_picker(self, vbox):
        icon_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon_row.set_border_width(4)
        icon_row.pack_start(Gtk.Label(label="Icon set:"), False, False, 0)

        self.icon_combo = Gtk.ComboBoxText()
        self.icon_sets  = get_icon_sets()
        current_set     = load_settings().get('icon_set', 'default').lower()

        active_idx = 0
        for i, name in enumerate(sorted(self.icon_sets.keys())):
            self.icon_combo.append_text(name)
            if name == current_set:
                active_idx = i
        if not self.icon_sets:
            self.icon_combo.append_text('(none found)')

        self.icon_combo.set_active(active_idx)
        self.icon_combo.connect('changed', self._on_icon_set_changed)
        icon_row.pack_start(self.icon_combo, False, False, 0)

        self.icon_preview_active   = Gtk.Image()
        self.icon_preview_inactive = Gtk.Image()
        icon_row.pack_start(Gtk.Label(label="Active:"),   False, False, 4)
        icon_row.pack_start(self.icon_preview_active,     False, False, 0)
        icon_row.pack_start(Gtk.Label(label="Inactive:"), False, False, 4)
        icon_row.pack_start(self.icon_preview_inactive,   False, False, 0)
        self.icon_preview_error = Gtk.Image()
        self.icon_preview_error.set_from_icon_name('dialog-error', Gtk.IconSize.SMALL_TOOLBAR)
        icon_row.pack_start(Gtk.Label(label="Error:"),    False, False, 4)
        icon_row.pack_start(self.icon_preview_error,      False, False, 0)

        vbox.pack_start(icon_row, False, False, 0)
        self._update_icon_previews(current_set)

    def _on_icon_set_changed(self, combo):
        self._update_icon_previews(combo.get_active_text())

    def _update_icon_previews(self, name):
        info = self.icon_sets.get(name, {})
        for img, key in [(self.icon_preview_active, 'active'),
                         (self.icon_preview_inactive, 'inactive'),
                         (self.icon_preview_error, 'error')]:
            path = info.get(key, '')
            if path and Path(path).exists():
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(path, 24, 24)
                    img.set_from_pixbuf(pb)
                except Exception:
                    img.set_from_icon_name('image-missing', Gtk.IconSize.SMALL_TOOLBAR)
            elif key == 'error':
                img.set_from_icon_name('dialog-error', Gtk.IconSize.SMALL_TOOLBAR)
            else:
                img.set_from_icon_name('image-missing', Gtk.IconSize.SMALL_TOOLBAR)

    def _refresh_list(self):
        self.store.clear()

        if not SETTINGS_PATH.exists():
            self._set_status("⚠ Settings file missing — showing defaults. Click 'Create Settings' to save.", transient=False)
        elif self.status_label.get_text().startswith("⚠ Settings file missing"):
            self._set_status("")

        configs = []
        if USER_CONFIG_DIR.exists():
            for p in sorted(USER_CONFIG_DIR.glob('*.config.json')):
                if p.name != 'settings.config.json':
                    configs.append(p)

        existing = {p.stem.replace('.config', '') for p in configs}

        cleaned = [c for c in self.enabled_configs if c in existing]
        if cleaned != self.enabled_configs:
            self.enabled_configs = cleaned
            self._save_enabled()

        for path in configs:
            name = path.stem.replace('.config', '')
            enabled = name in self.enabled_configs
            display = name
            desc = ''
            try:
                with open(path) as f:
                    cfg = json.load(f)
                    display = cfg.get('name', name)
                    desc = cfg.get('description', '')
            except Exception:
                pass
            self.store.append([enabled, display, desc, name, 'user'])

        n = len(self.enabled_configs)
        t = len(configs)
        self.status_label.set_text(f"{n} of {t} configs enabled")

    def _selected(self):
        return self.tree.get_selection().get_selected()

    def _config_path(self, name):
        p = USER_CONFIG_DIR / f"{name}.config.json"
        return p if p.exists() else None

    def _on_toggled(self, widget, path):
        it = self.store.get_iter(path)
        name = self.store[it][3]
        new_state = not self.store[it][0]

        if new_state:
            cfg_path = self._config_path(name)
            if cfg_path:
                err = self._validate_config(cfg_path)
                if err:
                    self._error(f"Cannot enable '{name}':\n{err}")
                    return

        self.store[it][0] = new_state
        if new_state:
            if name not in self.enabled_configs:
                self.enabled_configs.append(name)
        else:
            self.enabled_configs = [c for c in self.enabled_configs if c != name]

        self._save_enabled()
        self._refresh_list()
        self._check_dirty()

    def _on_new(self, widget):
        self._open_sequence_editor(None)

    def _on_row_activated(self, tree, path, column):
        if column == self.col_en:
            return
        self._on_edit(None)

    def _on_edit(self, widget):
        model, it = self._selected()
        if not it:
            self._error("Select a config to edit")
            return

        name = model[it][3]
        path = USER_CONFIG_DIR / f"{name}.config.json"
        if not path.exists():
            self._error("Config file not found")
            return

        self._open_sequence_editor(path)

    def _open_sequence_editor(self, path):
        """Open SequenceEditorDialog for the given config path (None for new)"""
        old_stem = Path(path).stem.replace('.config', '') if path else None

        def _on_saved():
            new_stem = dlg.seq_path.stem.replace('.config', '') if dlg.seq_path else None
            if old_stem and new_stem and new_stem != old_stem:
                self.enabled_configs = [
                    new_stem if c == old_stem else c
                    for c in self.enabled_configs
                ]
                self._save_enabled()
            self._refresh_list()
            result = subprocess.run(['systemctl', '--user', 'restart', 'umlaut'],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                self._set_status("✓ Config saved — daemon restarted")
            else:
                self._set_status("✓ Config saved")
                self._error(f"Daemon restart failed:\n{result.stderr.strip()}")
        dlg = SequenceEditorDialog(self, path, on_saved=_on_saved)
        dlg.show_all()

    def _on_move_up(self, _):
        model, it = self._selected()
        if not it:
            return
        prev = model.iter_previous(it)
        if prev:
            model.swap(it, prev)
            self._sync_order_from_store()

    def _on_move_down(self, _):
        model, it = self._selected()
        if not it:
            return
        next_ = model.iter_next(it)
        if next_:
            model.swap(it, next_)
            self._sync_order_from_store()

    def _sync_order_from_store(self):
        """Persist enabled config order from current store row order."""
        enabled_in_order = [row[3] for row in self.store if row[0]]
        self.enabled_configs = enabled_in_order + [
            c for c in self.enabled_configs if c not in enabled_in_order
        ]
        self._save_enabled()
        self._check_dirty()

    def _on_delete(self, widget):
        model, it = self._selected()
        if not it:
            self._error("Select a config to delete")
            return

        name = model[it][3]
        display = model[it][1]

        dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.YES_NO,
                                text=f"Delete '{display}'?")
        dlg.format_secondary_text("This cannot be undone.")
        resp = dlg.run()
        dlg.destroy()

        if resp != Gtk.ResponseType.YES:
            return

        try:
            was_enabled = name in self.enabled_configs
            (USER_CONFIG_DIR / f"{name}.config.json").unlink()
            self.enabled_configs = [c for c in self.enabled_configs if c != name]
            self._save_enabled()
            self._refresh_list()
            if was_enabled:
                result = subprocess.run(['systemctl', '--user', 'restart', 'umlaut'],
                                        capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    self._set_status("✓ Deleted — daemon restarted")
                else:
                    self._error(f"Daemon restart failed:\n{result.stderr.strip()}")
        except Exception as e:
            self._error(f"Failed to delete: {e}")

    def _on_apply(self, widget):
        errors = self._validate_enabled()
        if errors:
            msg = "Cannot apply — fix these configs first:\n\n"
            msg += "\n".join(f"• {n}: {e}" for n, e in errors)
            self._error(msg)
            return

        timeout = int(self.timeout_spin.get_value())
        loglevel = self.ll_combo.get_active_text()

        overrides = load_settings()
        overrides.setdefault('settings', {})
        overrides['settings']['timeout_ms'] = timeout
        overrides['settings']['log_level'] = loglevel
        overrides['passthrough_keys'] = self._pending_passthrough_keys
        overrides['trigger_key'] = self._get_trigger_key_list()
        icon_name = self.icon_combo.get_active_text()
        if icon_name and icon_name != '(none found)':
            overrides['icon_set'] = icon_name
        # sequences intentionally excluded from settings.config.json

        try:
            save_settings(overrides)
            self._disk_snapshot = self._snapshot(from_disk=True)
            self._settings_valid = True
            self._check_dirty()
        except Exception as e:
            self._error(f"Failed to save settings: {e}")
            return

        try:
            result = subprocess.run(['systemctl', '--user', 'restart', 'umlaut'],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                self._error(f"Daemon restart failed: {result.stderr}")
                return
        except Exception as e:
            self._error(f"Failed to restart daemon: {e}")
            return

        # Restart applet to pick up new icon/settings
        try:
            result = subprocess.run(['pgrep', '-f', 'umlaut_applet'],
                                    capture_output=True, text=True)
            my_pid = os.getpid()
            for pid_str in result.stdout.strip().splitlines():
                pid = int(pid_str)
                if pid != my_pid:
                    os.kill(pid, signal.SIGTERM)
            GLib.timeout_add(800, lambda: subprocess.Popen([str(APPLET_SCRIPT)]) and False)
        except Exception as e:
            logger.warning(f"Applet restart failed: {e}")

        self._set_status("✓ Changes applied — applet restarting")

    def _validate_config(self, path):
        """Validate a single config file. Returns error string or None."""
        try:
            with open(path) as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                return "Must be a JSON object"
            seqs = cfg.get('sequences', {})
            if not isinstance(seqs, dict):
                return "'sequences' must be a JSON object"
            for compose_key, targets in seqs.items():
                if isinstance(targets, str):
                    continue  # alias — valid
                if not isinstance(targets, dict):
                    return f"Sequence '{compose_key}' must be a dict or alias string, got {type(targets).__name__}"
                for target, output in targets.items():
                    if not isinstance(output, str):
                        return f"Output for '{compose_key}+{target}' must be a string"
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e.msg} at line {e.lineno}"
        except Exception as e:
            return str(e)
        return None

    def _validate_enabled(self):
        errors = []
        for name in self.enabled_configs:
            path = self._config_path(name)
            if not path:
                errors.append((name, "File not found"))
                continue
            err = self._validate_config(path)
            if err:
                errors.append((name, err))
        return errors

    def _error(self, msg):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True,
                                message_type=Gtk.MessageType.ERROR,
                                buttons=Gtk.ButtonsType.NONE, text="Error")
        dlg.format_secondary_text(msg)
        dlg.add_button("Copy to clipboard", Gtk.ResponseType.HELP)
        dlg.add_button("OK", Gtk.ResponseType.OK)
        while True:
            resp = dlg.run()
            if resp == Gtk.ResponseType.HELP:
                clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                clipboard.set_text(msg, -1)
            else:
                break
        dlg.destroy()


def main():
    # Inject CSS for test drawer result highlighting
    css = b"""
    entry.test-matched { background-color: #d4edda; color: #155724; }
    entry.test-nomatch  { background-color: #f8d7da; color: #721c24; }
    """
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    win = ConfigManager()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == '__main__':
    main()
