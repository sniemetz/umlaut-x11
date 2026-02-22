#!/usr/bin/env python3
"""
Unit tests for umlaut_daemon — parse methods and config validation.
Run from the service/ directory: python3 test_daemon.py
"""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure both service/ and root (umlaut_paths) are importable
sys.path.insert(0, str(Path(__file__).resolve().parent))           # service/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))  # lib/ (umlaut_paths)

# ---------------------------------------------------------------------------
# Mock evdev before importing daemon
# ---------------------------------------------------------------------------
def _make_evdev_mock():
    evdev = types.ModuleType("evdev")
    ecodes = types.ModuleType("evdev.ecodes")
    _key_names = [
        "KEY_A","KEY_B","KEY_C","KEY_D","KEY_E","KEY_F","KEY_G","KEY_H",
        "KEY_I","KEY_J","KEY_K","KEY_L","KEY_M","KEY_N","KEY_O","KEY_P",
        "KEY_Q","KEY_R","KEY_S","KEY_T","KEY_U","KEY_V","KEY_W","KEY_X",
        "KEY_Y","KEY_Z",
        "KEY_0","KEY_1","KEY_2","KEY_3","KEY_4",
        "KEY_5","KEY_6","KEY_7","KEY_8","KEY_9",
        "KEY_SPACE","KEY_MINUS","KEY_EQUAL","KEY_LEFTBRACE","KEY_RIGHTBRACE",
        "KEY_BACKSLASH","KEY_SEMICOLON","KEY_APOSTROPHE","KEY_GRAVE",
        "KEY_COMMA","KEY_DOT","KEY_SLASH",
        "KEY_ENTER","KEY_ESC","KEY_TAB","KEY_BACKSPACE",
        "KEY_LEFTSHIFT","KEY_RIGHTSHIFT","KEY_LEFTCTRL","KEY_RIGHTCTRL",
        "KEY_LEFTALT","KEY_RIGHTALT","KEY_LEFTMETA","KEY_RIGHTMETA",
        "KEY_F1","KEY_F2","KEY_F3","KEY_F4","KEY_F5","KEY_F6",
        "KEY_F7","KEY_F8","KEY_F9","KEY_F10","KEY_F11","KEY_F12",
        "KEY_UP","KEY_DOWN","KEY_LEFT","KEY_RIGHT",
        "KEY_HOME","KEY_END","KEY_PAGEUP","KEY_PAGEDOWN","KEY_DELETE",
        "KEY_CAPSLOCK","KEY_NUMLOCK","KEY_SCROLLLOCK",
        "EV_KEY","EV_SYN","SYN_REPORT",
    ]
    for i, name in enumerate(_key_names):
        setattr(ecodes, name, i + 1)
    ecodes.KEY = {i + 1: name for i, name in enumerate(_key_names)}
    evdev.ecodes = ecodes
    evdev.UInput = MagicMock()
    evdev.InputDevice = MagicMock()
    evdev.list_devices = MagicMock(return_value=[])
    sys.modules["evdev"] = evdev
    sys.modules["evdev.ecodes"] = ecodes
    return evdev

_make_evdev_mock()
sys.modules.setdefault("inotify_simple", MagicMock())

import umlaut_daemon as _dm

UmlautConfig = _dm.UmlautConfig
OutputAction  = _dm.OutputAction
ecodes        = _dm.e


def make_config():
    with patch.object(UmlautConfig, "load_config", lambda self: None):
        cfg = UmlautConfig()
    cfg.trigger_keys_list = [ecodes.KEY_LEFTALT]
    cfg.passthrough_keys  = []
    return cfg


class TestParseTargetKey(unittest.TestCase):

    def setUp(self):
        self.cfg = make_config()

    def test_lowercase_letter(self):
        self.assertEqual(self.cfg._parse_target_key("a"), [ecodes.KEY_A])

    def test_uppercase_adds_shift(self):
        self.assertEqual(self.cfg._parse_target_key("A"),
                         [ecodes.KEY_LEFTSHIFT, ecodes.KEY_A])

    def test_digit(self):
        self.assertEqual(self.cfg._parse_target_key("5"), [ecodes.KEY_5])

    def test_semicolon(self):
        self.assertEqual(self.cfg._parse_target_key(";"), [ecodes.KEY_SEMICOLON])

    def test_apostrophe(self):
        self.assertEqual(self.cfg._parse_target_key("'"), [ecodes.KEY_APOSTROPHE])

    def test_shifted_exclamation(self):
        self.assertEqual(self.cfg._parse_target_key("!"),
                         [ecodes.KEY_LEFTSHIFT, ecodes.KEY_1])

    def test_key_name_enter(self):
        self.assertEqual(self.cfg._parse_target_key("KEY_ENTER"), [ecodes.KEY_ENTER])

    def test_key_name_space(self):
        self.assertEqual(self.cfg._parse_target_key("KEY_SPACE"), [ecodes.KEY_SPACE])

    def test_unknown_char_raises(self):
        with self.assertRaises(ValueError):
            self.cfg._parse_target_key("£")

    def test_unknown_key_name_raises(self):
        with self.assertRaises(Exception):
            self.cfg._parse_target_key("KEY_DOESNOTEXIST")

    def test_ctrl_combo(self):
        result = self.cfg._parse_target_key("CTRL+a")
        self.assertIn(ecodes.KEY_LEFTCTRL, result)
        self.assertIn(ecodes.KEY_A, result)


class TestParseOutput(unittest.TestCase):

    def setUp(self):
        self.cfg = make_config()

    def test_string(self):
        r = self.cfg._parse_output("hello")
        self.assertEqual(r.action_type, "string")
        self.assertEqual(r.data, "hello")

    def test_unicode(self):
        r = self.cfg._parse_output("ä")
        self.assertEqual(r.action_type, "string")

    def test_empty_string(self):
        r = self.cfg._parse_output("")
        self.assertEqual(r.data, "")

    def test_too_long_raises(self):
        with self.assertRaises(ValueError):
            self.cfg._parse_output("x" * 10001)

    def test_list(self):
        r = self.cfg._parse_output(["KEY_A", "KEY_B"])
        self.assertEqual(r.action_type, "sequence")
        self.assertEqual(len(r.data), 2)

    def test_list_too_deep_raises(self):
        with self.assertRaises(ValueError):
            self.cfg._parse_output(["a"] * 11)


class TestLoadSingleConfig(unittest.TestCase):

    def setUp(self):
        self.cfg = make_config()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, data):
        p = Path(self.tmp.name) / "test.config.json"
        p.write_text(json.dumps(data))
        return str(p)

    def test_valid_sequences_loaded(self):
        path = self._write({"sequences": {";": {"a": "ä"}}})
        self.cfg._load_single_config(path, is_system=False)
        self.assertGreater(len(self.cfg.sequences), 0)

    def test_invalid_json_skipped(self):
        p = Path(self.tmp.name) / "bad.json"
        p.write_text("not valid json {{")
        before = len(self.cfg.sequences)
        self.cfg._load_single_config(str(p), is_system=False)
        self.assertEqual(len(self.cfg.sequences), before)

    def test_sequences_not_dict_skipped(self):
        path = self._write({"sequences": ["a", "b"]})
        before = len(self.cfg.sequences)
        self.cfg._load_single_config(path, is_system=False)
        self.assertEqual(len(self.cfg.sequences), before)

    def test_missing_file_skipped(self):
        before = len(self.cfg.sequences)
        self.cfg._load_single_config("/nonexistent.json", is_system=False)
        self.assertEqual(len(self.cfg.sequences), before)

    def test_bad_compose_key_does_not_abort_file(self):
        path = self._write({"sequences": {
            "£": {"a": "bad"},
            ";": {"a": "ä"},
        }})
        self.cfg._load_single_config(path, is_system=False)
        self.assertGreater(len(self.cfg.sequences), 0)

    def test_alias_resolves(self):
        path = self._write({"sequences": {
            ";":       {"a": "ä"},
            "SHIFT+;": ";",
        }})
        self.cfg._load_single_config(path, is_system=False)
        shifted = [k for k in self.cfg.sequences if k[1] is True]
        self.assertTrue(len(shifted) > 0)

    def test_output_value_preserved(self):
        path = self._write({"sequences": {";": {"a": "ä"}}})
        self.cfg._load_single_config(path, is_system=False)
        seq = next(iter(self.cfg.sequences.values()))
        self.assertEqual(seq.output.data, "ä")




# ---------------------------------------------------------------------------
# umlaut_paths helpers
# ---------------------------------------------------------------------------

# Mock gi for umlaut_paths (it imports logging only, but guard anyway)
import umlaut_paths as _up


class TestApplySchema(unittest.TestCase):

    def test_fills_missing_keys(self):
        result = _up._apply_schema({}, _up.SETTINGS_SCHEMA)
        self.assertEqual(result['enabled_sequences'], [])
        self.assertEqual(result['settings']['timeout_ms'], 1000)

    def test_drops_unknown_keys(self):
        result = _up._apply_schema({'unknown_key': 'x'}, _up.SETTINGS_SCHEMA)
        self.assertNotIn('unknown_key', result)

    def test_preserves_known_keys(self):
        result = _up._apply_schema({'icon_set': 'mytheme'}, _up.SETTINGS_SCHEMA)
        self.assertEqual(result['icon_set'], 'mytheme')

    def test_nested_fill(self):
        result = _up._apply_schema({'settings': {}}, _up.SETTINGS_SCHEMA)
        self.assertEqual(result['settings']['log_level'], 'INFO')

    def test_nested_drop_unknown(self):
        result = _up._apply_schema({'settings': {'timeout_ms': 500, 'extra': 1}}, _up.SETTINGS_SCHEMA)
        self.assertNotIn('extra', result['settings'])
        self.assertEqual(result['settings']['timeout_ms'], 500)


    def test_unknown_settings_keys_logged(self):
        """load_settings warns when file contains keys not in schema"""
        import tempfile, unittest.mock
        data = {'trigger_key': ['KEY_LEFTALT'], 'mystery_field': 'oops'}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            import json as _json
            _json.dump(data, f)
            tmp = f.name
        with unittest.mock.patch.object(_up, 'USER_SETTINGS', Path(tmp)):
            with self.assertLogs('umlaut', level='WARNING') as cm:
                result = _up.load_settings()
        self.assertNotIn('mystery_field', result)
        self.assertTrue(any('mystery_field' in m for m in cm.output))
        import os; os.unlink(tmp)

class TestLoadSequenceConfig(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, data):
        p = Path(self.tmp.name) / 'test.config.json'
        p.write_text(json.dumps(data))
        return str(p)

    def test_valid_config(self):
        path = self._write({'name': 'Test', 'sequences': {';': {'a': 'ä'}}})
        result = _up.load_sequence_config(path)
        self.assertIsNotNone(result)
        self.assertEqual(result['sequences'][';']['a'], 'ä')

    def test_drops_unknown_top_level_keys(self):
        path = self._write({'name': 'Test', 'sequences': {}, 'junk': 123})
        result = _up.load_sequence_config(path)
        self.assertNotIn('junk', result)

    def test_missing_file_returns_none(self):
        self.assertIsNone(_up.load_sequence_config('/nonexistent.json'))

    def test_invalid_json_returns_none(self):
        p = Path(self.tmp.name) / 'bad.json'
        p.write_text('{{not json')
        self.assertIsNone(_up.load_sequence_config(str(p)))

    def test_non_dict_root_returns_none(self):
        path = self._write([1, 2, 3])
        self.assertIsNone(_up.load_sequence_config(path))

    def test_drops_non_dict_compose_entry(self):
        path = self._write({'sequences': {';': [1, 2, 3], "'": {'e': 'é'}}})
        result = _up.load_sequence_config(path)
        self.assertNotIn(';', result['sequences'])
        self.assertIn("'", result['sequences'])

    def test_drops_non_string_output(self):
        path = self._write({'sequences': {';': {'a': 'ä', 'b': 123}}})
        result = _up.load_sequence_config(path)
        self.assertIn('a', result['sequences'][';'])
        self.assertNotIn('b', result['sequences'][';'])

    def test_drops_empty_targets(self):
        path = self._write({'sequences': {';': {}}})
        result = _up.load_sequence_config(path)
        self.assertNotIn(';', result['sequences'])

    def test_valid_alias_kept(self):
        path = self._write({'sequences': {';': {'a': 'ä'}, 'SHIFT+;': ';'}})
        result = _up.load_sequence_config(path)
        self.assertEqual(result['sequences']['SHIFT+;'], ';')

    def test_broken_alias_dropped(self):
        path = self._write({'sequences': {'SHIFT+;': ';'}})  # ';' doesn't exist
        result = _up.load_sequence_config(path)
        self.assertNotIn('SHIFT+;', result['sequences'])

    def test_sequences_not_dict_resets(self):
        path = self._write({'sequences': ['a', 'b']})
        result = _up.load_sequence_config(path)
        self.assertEqual(result['sequences'], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
