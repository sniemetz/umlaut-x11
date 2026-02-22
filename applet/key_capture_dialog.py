#!/usr/bin/env python3
"""
Umlaut Key Sequence Editor Dialog
- Top: trigger key config (writes to settings.config.json)
- Bottom: sequence editor (writes to sequence config)
Key capture via window-level key-press-event.
hardware_keycode - 8 = evdev keycode (linux/input-event-codes.h)
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk
import json
from pathlib import Path

from umlaut_paths import USER_CONFIG_DIR, USER_SETTINGS as SETTINGS_PATH

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
        label = base             # fallback: raw name
    return f'Shift+{label}' if shift else label


def hw_to_evdev(hw_keycode):
    """X11 hardware_keycode -> evdev KEY_* name (no shift handling — raw key only)"""
    return EVDEV_TO_NAME.get(hw_keycode - 8)


def evdev_to_target(evdev_name):
    """KEY_A -> 'a', KEY_SEMICOLON -> 'KEY_SEMICOLON'"""
    if evdev_name and evdev_name.startswith('KEY_') and len(evdev_name) == 5:
        return evdev_name[4].lower()
    return evdev_name


class KeyCaptureButton(Gtk.Button):
    """
    Button that captures the next keypress when clicked.
    Emits 'key-captured' signal with evdev name.
    allow_modifiers: if True, accepts bare modifier keys (for trigger key field)
    allow_shift: if True, prefixes SHIFT+ for shifted keys (for compose/target)
    """

    def __init__(self, label="Click to capture", allow_modifiers=False, allow_shift=True):
        super().__init__(label=label)
        self.allow_modifiers = allow_modifiers
        self.allow_shift     = allow_shift
        self._capturing      = False
        self._dialog         = None
        self._handler_id     = None
        self.evdev           = None
        self.connect('clicked', self._on_clicked)

    def _on_clicked(self, _):
        self._capturing = True
        self.set_label("[ press a key… ]")
        self.set_sensitive(False)
        # Connect to the parent dialog's key-press-event
        self._dialog = self.get_toplevel()
        self._handler_id = self._dialog.connect('key-press-event', self._on_key)

    def _on_key(self, widget, event):
        if not self._capturing:
            return False

        hw = event.hardware_keycode
        evdev = hw_to_evdev(hw)

        if not self.allow_modifiers and evdev in MODIFIER_EVDEV:
            # Ignore pure modifier press in non-modifier fields
            return True

        if evdev:
            if self.allow_shift:
                shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
                if shift and not self.allow_modifiers:
                    evdev = f'SHIFT+{evdev}'
            self.evdev = evdev
            self.set_label(evdev_to_display(evdev))
        else:
            self.evdev = None
            self.set_label(f"? (hw={hw})")

        self._stop_capture()
        return True

    def _stop_capture(self):
        self._capturing = False
        self.set_sensitive(True)
        if self._handler_id and self._dialog:
            self._dialog.disconnect(self._handler_id)
            self._handler_id = None
            self._dialog = None

    def reset(self, placeholder="Click to capture"):
        self._stop_capture()
        self.evdev = None
        self.set_label(placeholder)


class SequenceEditorDialog(Gtk.Dialog):

    def __init__(self, parent, sequence_config_path):
        super().__init__(title="Edit Sequence", transient_for=parent, modal=True)
        self.set_default_size(620, 580)

        self.seq_path   = Path(sequence_config_path)
        self.seq_config = self._load_json(self.seq_path, {"sequences": {}})
        self.settings   = self._load_json(SETTINGS_PATH, {})

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect('clicked', lambda _: self.destroy())
        self.get_action_area().pack_start(btn_cancel, False, False, 0)
        self.get_action_area().reorder_child(btn_cancel, 0)
        self.btn_save = Gtk.Button(label="Save")
        self.btn_save.connect('clicked', self._on_save_clicked)
        self.get_action_area().pack_start(self.btn_save, False, False, 0)

        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)

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
        seq_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        seq_vbox.set_border_width(8)
        seq_frame.add(seq_vbox)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(150)

        self.store = Gtk.ListStore(str, str, str)
        self.tree  = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)
        for i, title in enumerate(["Compose Key", "Target", "Output"]):
            r   = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, r, text=i)
            col.set_expand(True)
            self.tree.append_column(col)
        self.tree.get_selection().connect('changed', self._on_sel_changed)
        scroll.add(self.tree)
        seq_vbox.pack_start(scroll, True, True, 0)

        list_btns = Gtk.Box(spacing=4)
        self.btn_delete = Gtk.Button(label="Delete Selected")
        self.btn_delete.set_sensitive(False)
        self.btn_delete.connect('clicked', self._on_delete)
        list_btns.pack_end(self.btn_delete, False, False, 0)
        seq_vbox.pack_start(list_btns, False, False, 0)

        seq_vbox.pack_start(Gtk.Separator(), False, False, 2)
        seq_vbox.pack_start(Gtk.Label(label="Add / Edit Sequence:", xalign=0), False, False, 0)

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

        seq_vbox.pack_start(grid, False, False, 0)

        self.status_label = Gtk.Label(label="", xalign=0)
        seq_vbox.pack_start(self.status_label, False, False, 0)

        btn_row = Gtk.Box(spacing=6)
        self.btn_add = Gtk.Button(label="Add to List")
        self.btn_add.connect('clicked', self._on_add)
        btn_row.pack_start(self.btn_add, False, False, 0)
        btn_clear = Gtk.Button(label="Clear Form")
        btn_clear.connect('clicked', lambda _: self._reset_form())
        btn_row.pack_start(btn_clear, False, False, 0)
        seq_vbox.pack_start(btn_row, False, False, 0)

        box.pack_start(seq_frame, True, True, 0)

        self._refresh_list()
        self.show_all()
        if initial_trigger not in BOTH_PAIRS:
            self.both_check.hide()
        self._orig_snapshot = self._snapshot()
        self._saved = False
        self.both_check.connect('toggled', self._on_state_changed)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _load_json(self, path, default):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return default

    # ── Sequences ─────────────────────────────────────────────────────────

    def _refresh_list(self):
        self.store.clear()
        for compose_key, targets in self.seq_config.get('sequences', {}).items():
            if isinstance(targets, dict):
                for target, output in targets.items():
                    self.store.append([compose_key, target, str(output)])
            elif isinstance(targets, str):
                self.store.append([compose_key, '→ alias', targets])

    def _on_sel_changed(self, selection):
        model, it = selection.get_selected()
        self.btn_delete.set_sensitive(it is not None)
        if it:
            self.btn_compose.evdev = model[it][0]
            self.btn_compose.set_label(evdev_to_display(model[it][0]))
            self.btn_target.evdev = model[it][1]
            self.btn_target.set_label(evdev_to_display(model[it][1]))
            self.output_entry.set_text(model[it][2])
            self.btn_add.set_label("Save")
            self.status_label.set_text("Editing selected row — update values and click Save")
        else:
            self.btn_add.set_label("Add to List")

    def _on_delete(self, _):
        _, it = self.tree.get_selection().get_selected()
        if not it:
            return
        compose_key = self.store[it][0]
        target      = self.store[it][1]
        seqs = self.seq_config.setdefault('sequences', {})
        if compose_key in seqs and isinstance(seqs[compose_key], dict):
            seqs[compose_key].pop(target, None)
            if not seqs[compose_key]:
                del seqs[compose_key]
        self._refresh_list()
        self._reset_form()
        self.status_label.set_text("Deleted.")
        self._on_state_changed()

    def _on_add(self, _):
        compose = self.btn_compose.evdev
        target  = self.btn_target.evdev
        output  = self.output_entry.get_text().strip()

        if not compose:
            self.status_label.set_text("⚠ Capture a compose key first")
            return
        if not target:
            self.status_label.set_text("⚠ Capture a target key first")
            return
        if not output:
            self.status_label.set_text("⚠ Enter an output value")
            return

        target_cfg = evdev_to_target(target)
        self.seq_config.setdefault('sequences', {}).setdefault(compose, {})[target_cfg] = output
        self._refresh_list()
        self.status_label.set_text(f"✓ Added: {compose} + {target_cfg} → {output}")
        self._reset_form()
        self._on_state_changed()

    def _reset_form(self):
        self.btn_compose.reset()
        self.btn_target.reset()
        self.output_entry.set_text("")
        self.status_label.set_text("")
        self.btn_add.set_label("Add to List")
        self.tree.get_selection().unselect_all()

    def _snapshot(self):
        import json as _json
        return _json.dumps({
            'name': self.name_entry.get_text(),
            'description': self.desc_entry.get_text(),
            'sequences': self.seq_config.get('sequences', {})
        }, sort_keys=True)

    def _on_save_clicked(self, _):
        ok, err = self.save()
        if ok:
            self._saved = True
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
        self.seq_config['name'] = self.name_entry.get_text()
        self.seq_config['description'] = self.desc_entry.get_text()
        try:
            with open(self.seq_path, 'w') as f:
                json.dump(self.seq_config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            return False, f"Failed to save sequences: {e}"
        return True, None

    def get_sequences(self):
        return self.seq_config.get('sequences', {})


def main():
    config_path = USER_CONFIG_DIR / 'shortcuts.config.json'
    if not config_path.exists():
        config_path = next(iter(USER_CONFIG_DIR.glob('*.config.json')), None)
    if not config_path:
        print(f"No config found in {USER_CONFIG_DIR}")
        return

    win = Gtk.Window(title="Sequence Editor Test")
    win.connect('destroy', Gtk.main_quit)
    win.show()

    dlg = SequenceEditorDialog(win, config_path)
    resp = dlg.run()
    if resp == Gtk.ResponseType.OK:
        ok, err = dlg.save()
        print("Saved." if ok else f"Error: {err}")
    dlg.destroy()
    Gtk.main_quit()


if __name__ == '__main__':
    main()
