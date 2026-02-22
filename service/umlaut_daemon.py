#!/usr/bin/env python3
"""
Umlaut Daemon - System-wide keyboard remapping with multi-key sequences
Intercepts keyboard events at /dev/input level before they reach applications
"""

import evdev
import json
import os
import sys
import signal
import ctypes
import struct
import select
import time
import subprocess
import logging
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from evdev import UInput, ecodes as e

# Import shared path helpers (umlaut_paths.py installed alongside this script)
sys.path.insert(0, str(Path(__file__).parent))
try:
    from umlaut_paths import load_sequence_config
except ImportError:
    load_sequence_config = None  # graceful degradation if not installed yet

# Logging will be configured in main() based on args
logger = logging.getLogger('umlaut')

def _fatal_error(msg: str):
    """Log error and exit non-zero so systemd marks unit failed."""
    logger.error(msg)
    raise SystemExit(f"FATAL: {msg}")


@dataclass
class OutputAction:
    """Represents an output action"""
    action_type: str  # 'key', 'string', 'sequence'
    data: any  # Depends on type


@dataclass
class KeySequence:
    """Represents a key sequence mapping"""
    modifier_keys: List[int]  # Trigger key codes (e.g., KEY_LEFTALT)
    compose_key: int          # Compose key code (e.g., KEY_SEMICOLON)
    compose_shifted: bool     # Whether compose key requires Shift
    target_keys: List[int]    # Target key codes including modifiers
    output: OutputAction      # What to output


class UmlautConfig:
    """Handles configuration loading and parsing"""
    
    # Character to KEY_ mapping for common characters
    CHAR_TO_KEY = {
        'a': e.KEY_A, 'b': e.KEY_B, 'c': e.KEY_C, 'd': e.KEY_D, 'e': e.KEY_E,
        'f': e.KEY_F, 'g': e.KEY_G, 'h': e.KEY_H, 'i': e.KEY_I, 'j': e.KEY_J,
        'k': e.KEY_K, 'l': e.KEY_L, 'm': e.KEY_M, 'n': e.KEY_N, 'o': e.KEY_O,
        'p': e.KEY_P, 'q': e.KEY_Q, 'r': e.KEY_R, 's': e.KEY_S, 't': e.KEY_T,
        'u': e.KEY_U, 'v': e.KEY_V, 'w': e.KEY_W, 'x': e.KEY_X, 'y': e.KEY_Y,
        'z': e.KEY_Z,
        '0': e.KEY_0, '1': e.KEY_1, '2': e.KEY_2, '3': e.KEY_3, '4': e.KEY_4,
        '5': e.KEY_5, '6': e.KEY_6, '7': e.KEY_7, '8': e.KEY_8, '9': e.KEY_9,
        ' ': e.KEY_SPACE, '-': e.KEY_MINUS, '=': e.KEY_EQUAL,
        '[': e.KEY_LEFTBRACE, ']': e.KEY_RIGHTBRACE, '\\': e.KEY_BACKSLASH,
        ';': e.KEY_SEMICOLON, "'": e.KEY_APOSTROPHE, '`': e.KEY_GRAVE,
        ',': e.KEY_COMMA, '.': e.KEY_DOT, '/': e.KEY_SLASH,
    }
    
    # Shifted characters
    SHIFTED_CHARS = {
        'A': ('a', True), 'B': ('b', True), 'C': ('c', True), 'D': ('d', True),
        'E': ('e', True), 'F': ('f', True), 'G': ('g', True), 'H': ('h', True),
        'I': ('i', True), 'J': ('j', True), 'K': ('k', True), 'L': ('l', True),
        'M': ('m', True), 'N': ('n', True), 'O': ('o', True), 'P': ('p', True),
        'Q': ('q', True), 'R': ('r', True), 'S': ('s', True), 'T': ('t', True),
        'U': ('u', True), 'V': ('v', True), 'W': ('w', True), 'X': ('x', True),
        'Y': ('y', True), 'Z': ('z', True),
        '!': ('1', True), '@': ('2', True), '#': ('3', True), '$': ('4', True),
        '%': ('5', True), '^': ('6', True), '&': ('7', True), '*': ('8', True),
        '(': ('9', True), ')': ('0', True),
        '_': ('-', True), '+': ('=', True), '{': ('[', True), '}': (']', True),
        '|': ('\\', True), ':': (';', True), '"': ("'", True), '~': ('`', True),
        '<': (',', True), '>': ('.', True), '?': ('/', True),
    }
    
    def __init__(self):
        self.sequences: Dict[Tuple[int, ...], KeySequence] = {}
        self.trigger_keys_list: List[int] = []
        self.passthrough_keys: List[int] = []  # Keys that abort compose even if mapped
        # Settings with defaults
        self.timeout_ms: int = 1000
        self.log_level: str = 'INFO'
        self.load_config()
    
    def load_config(self):
        """Load configuration.

        Load order:
        1. ~/.config/umlaut/settings.config.json — trigger key, passthrough keys, settings, enabled_sequences
        2. Sequence configs listed in enabled_sequences
        """
        self.sequences.clear()
        self.trigger_keys_list.clear()
        self.passthrough_keys.clear()

        # Load settings (trigger key, passthrough keys, timeout etc.)
        settings_path = Path.home() / '.config' / 'umlaut' / 'settings.config.json'
        logger.info(f"Loading settings: {settings_path}")
        self._load_single_config(str(settings_path), is_system=True, sequences_allowed=False)

        # Load enabled sequence configs
        self._load_enabled_configs()

        logger.info(f"Total loaded: {len(self.sequences)} key sequences")

        # Fatal validation
        if not self.trigger_keys_list:
            _fatal_error("No trigger key defined. Check settings.config.json.")
        if not self.sequences:
            _fatal_error("No sequences loaded. Enable at least one sequence config.")
    
    def _load_single_config(self, config_path: str, is_system: bool = True, sequences_allowed: bool = True):
        """Load and parse a single JSON configuration file
        
        Args:
            config_path: Path to the config file
            is_system: True if this is the system config (sets trigger_key/passthrough_keys)
                      False if user config (only adds sequences)
            sequences_allowed: If False, sequences in this config are ignored
        """
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
        except FileNotFoundError:
            if is_system:
                _fatal_error(f"Config file not found: {config_path}")
            else:
                logger.warning(f"User config file not found: {config_path}")
                return
        except json.JSONDecodeError as e:
            if is_system:
                _fatal_error(f"Invalid JSON in config file {config_path}: {e}")
            else:
                logger.error(f"Invalid JSON in user config {config_path}: {e}")
                return
        
        # Validate config structure
        if not isinstance(config, dict):
            logger.error(f"Config must be a JSON object, not {type(config).__name__}")
            return

        # Version check — warn but continue (forward-compatible)
        CONFIG_VERSION = 1
        version = config.get('version')
        if version is None:
            logger.warning(f"Config {config_path} has no version field (expected {CONFIG_VERSION})")
        elif version != CONFIG_VERSION:
            logger.warning(f"Config {config_path} version {version} != expected {CONFIG_VERSION}")

        # Parse trigger keys (any config can override - last wins)
        trigger_key_def = config.get('trigger_key')
        if trigger_key_def:
            if isinstance(trigger_key_def, str):
                trigger_key_def = [trigger_key_def]
            
            # Clear existing modifiers when overriding
            self.trigger_keys_list.clear()
            
            for mod_key in trigger_key_def:
                try:
                    key_code = self._key_name_to_code(mod_key)
                    self.trigger_keys_list.append(key_code)
                except ValueError as e:
                    logger.error(f"Unknown trigger key: {mod_key}")
                    if is_system:
                        _fatal_error(f"Unknown trigger key '{mod_key}' - check your config file")
            
            logger.info(f"Trigger keys set to: {trigger_key_def}")
        
        # Parse passthrough_keys — settings-level only, not from sequence configs
        if is_system:
            ignore_def = config.get('passthrough_keys')
            if ignore_def:
                self.passthrough_keys.clear()
                for pt_key in ignore_def:
                    try:
                        key_code = self._parse_target_key(pt_key)[0]
                        self.passthrough_keys.append(key_code)
                    except ValueError as e:
                        logger.warning(f"Unknown passthrough key '{pt_key}': {e}")
                if self.passthrough_keys:
                    logger.info(f"Ignore keys set to: {ignore_def}")
        
        # Parse settings (system config only; ignored for sequence-only user configs)
        settings = config.get('settings', {}) if is_system else {}
        if settings:
            if 'timeout_ms' in settings:
                try:
                    value = int(settings['timeout_ms'])
                    # Validate range: 100ms to 10s (reasonable for compose sequences)
                    if 100 <= value <= 10000:
                        self.timeout_ms = value
                        self.timeout_sec = self.timeout_ms / 1000.0
                        logger.info(f"Compose timeout: {self.timeout_ms}ms")
                    else:
                        logger.warning(f"Invalid timeout_ms {value} (must be 100-10000), keeping default {self.timeout_ms}ms")
                except (ValueError, TypeError) as e:
                    logger.warning(f"Invalid timeout_ms value: {e}, keeping default {self.timeout_ms}ms")
            
            if 'log_level' in settings:
                self.log_level = settings['log_level'].upper()
                logger.info(f"Log level: {self.log_level}")
        
        # Parse sequences (skipped for settings-only configs)
        if not sequences_allowed:
            return
        sequences_def = config.get('sequences', {})
        
        # Validate sequences structure
        if not isinstance(sequences_def, dict):
            logger.error(f"'sequences' must be a dict, not {type(sequences_def).__name__}")
            return
        
        # Limit number of sequences to prevent DoS
        MAX_SEQUENCES = 10000
        if len(sequences_def) > MAX_SEQUENCES:
            logger.error(f"Too many sequences ({len(sequences_def)}), max is {MAX_SEQUENCES}")
            return
        
        sequences_added = 0
        
        for compose_key_name, targets in sequences_def.items():
            try:
                # Check if this is an alias (string value instead of dict)
                if isinstance(targets, str):
                    alias_target = targets
                    logger.debug(f"'{compose_key_name}' is an alias to '{alias_target}'")
                    
                    # Resolve the alias
                    if alias_target not in sequences_def:
                        logger.warning(f"Alias '{compose_key_name}' references unknown compose key '{alias_target}' - skipping")
                        continue
                    
                    # Use the target's sequences
                    targets = sequences_def[alias_target]
                    
                    # If the alias target is also a string (chained alias), skip
                    if isinstance(targets, str):
                        logger.warning(f"Alias '{compose_key_name}' points to another alias '{alias_target}' - chained aliases not supported")
                        continue
                
                # Check if compose key has SHIFT+ prefix
                compose_shifted = False
                if compose_key_name.upper().startswith('SHIFT+'):
                    compose_shifted = True
                    compose_key_name = compose_key_name[6:]  # Remove "SHIFT+"
                
                compose_key = self._parse_target_key(compose_key_name)
                compose_key_code = compose_key[0]
                
                logger.debug(f"Parsing compose key: {compose_key_name} (shifted={compose_shifted})")
                
                for target_name, output_def in targets.items():
                    try:
                        target_keys = self._parse_target_key(target_name)
                        output_action = self._parse_output(output_def)
                        
                        # Build lookup key
                        for mod_key in self.trigger_keys_list:
                            lookup_key = (mod_key, compose_shifted, compose_key_code, *target_keys)
                            
                            # Add or override sequence
                            self.sequences[lookup_key] = KeySequence(
                                modifier_keys=[mod_key],
                                compose_key=compose_key_code,
                                compose_shifted=compose_shifted,
                                target_keys=target_keys,
                                output=output_action
                            )
                            sequences_added += 1
                        
                        logger.debug(f"  {target_name} -> {output_def}")
                    
                    except ValueError as e:
                        logger.warning(f"Skipping sequence {compose_key_name}+{target_name}: {e}")
                        continue
            
            except ValueError as e:
                logger.warning(f"Skipping entire compose key '{compose_key_name}': {e}")
                continue
        
        config_type = "system" if is_system else "user"
        logger.info(f"Loaded {sequences_added} sequences from {config_type} config")
    
    def _load_sequence_file(self, config_path: str):
        """Load a sequence config file using umlaut_paths.load_sequence_config,
        then feed the cleaned data into the existing sequence parser."""
        if load_sequence_config is None:
            # Fallback: use old path directly
            self._load_single_config(config_path, is_system=False)
            return

        cfg = load_sequence_config(config_path)
        if cfg is None:
            logger.warning(f"Skipping unreadable sequence config: {config_path}")
            return

        sequences_def = cfg.get('sequences', {})
        if not sequences_def:
            logger.debug(f"No sequences in {config_path}")
            return

        sequences_added = 0
        for compose_key_name, targets in sequences_def.items():
            try:
                # Alias — already validated by load_sequence_config
                if isinstance(targets, str):
                    targets = sequences_def.get(targets)
                    if not isinstance(targets, dict):
                        continue

                compose_shifted = False
                if compose_key_name.upper().startswith('SHIFT+'):
                    compose_shifted = True
                    compose_key_name = compose_key_name[6:]

                compose_key = self._parse_target_key(compose_key_name)
                compose_key_code = compose_key[0]

                for target_name, output_def in targets.items():
                    try:
                        target_keys = self._parse_target_key(target_name)
                        output_action = self._parse_output(output_def)
                        for mod_key in self.trigger_keys_list:
                            lookup_key = (mod_key, compose_shifted, compose_key_code, *target_keys)
                            self.sequences[lookup_key] = KeySequence(
                                modifier_keys=[mod_key],
                                compose_key=compose_key_code,
                                compose_shifted=compose_shifted,
                                target_keys=target_keys,
                                output=output_action
                            )
                            sequences_added += 1
                    except ValueError as ex:
                        logger.warning(f"Skipping {compose_key_name}+{target_name}: {ex}")
            except ValueError as ex:
                logger.warning(f"Skipping compose key '{compose_key_name}': {ex}")

        logger.info(f"Loaded {sequences_added} sequences from {config_path}")

    def _load_enabled_configs(self):
        """Load sequence configs listed in enabled_sequences from settings.config.json"""
        settings_path = Path.home() / '.config' / 'umlaut' / 'settings.config.json'
        user_config_dir = Path.home() / '.config' / 'umlaut'

        try:
            with open(settings_path) as f:
                settings = json.load(f)
            enabled_configs = settings.get('enabled_sequences', [])
        except FileNotFoundError:
            logger.debug("settings.config.json not found — no sequence configs loaded")
            return
        except Exception as e:
            logger.warning(f"Failed to read enabled_sequences from settings: {e}")
            return

        if not enabled_configs:
            logger.debug("No configs enabled")
            return

        logger.info(f"Enabled configs: {len(enabled_configs)}")
        for config_name in enabled_configs:
            config_path = user_config_dir / f"{config_name}.config.json"
            if not config_path.exists():
                logger.warning(f"Config '{config_name}' not found in {user_config_dir}")
                continue
            logger.info(f"Loading config: {config_name}")
            try:
                self._load_sequence_file(str(config_path))
            except Exception as e:
                logger.error(f"Failed to load config '{config_name}': {e}")
    
    def _parse_target_key(self, target: str) -> List[int]:
        """Parse target key notation like 'a', 'A', '$', 'CTRL+o' into key codes"""
        key_codes = []
        
        # Check for modifier+key notation (e.g., "CTRL+o")
        if '+' in target and any(mod in target.upper() for mod in ['CTRL', 'ALT', 'SHIFT', 'META']):
            parts = target.split('+')
            for part in parts:
                key_codes.append(self._char_or_key_to_code(part.strip()))
            return key_codes
        
        # Single character
        if len(target) == 1:
            char = target
            
            # Check if it's a shifted character
            if char in self.SHIFTED_CHARS:
                base_char, _ = self.SHIFTED_CHARS[char]
                key_codes.append(e.KEY_LEFTSHIFT)
                key_codes.append(self.CHAR_TO_KEY[base_char])
            elif char in self.CHAR_TO_KEY:
                key_codes.append(self.CHAR_TO_KEY[char])
            else:
                raise ValueError(f"Unknown character: {char}")
        else:
            # Multi-character, assume it's a KEY_ name
            key_codes.append(self._key_name_to_code(target))
        
        return key_codes
    
    def _char_or_key_to_code(self, text: str) -> int:
        """Convert a character or key name to key code"""
        # Check if it's a modifier name (shorthand or full)
        text_upper = text.upper()
        
        # Shorthand modifiers
        if text_upper == 'CTRL':
            return e.KEY_LEFTCTRL
        elif text_upper == 'ALT':
            return e.KEY_LEFTALT
        elif text_upper == 'ALTGR':
            return e.KEY_RIGHTALT
        elif text_upper == 'SHIFT':
            return e.KEY_LEFTSHIFT
        elif text_upper == 'META' or text_upper == 'SUPER':
            return e.KEY_LEFTMETA
        
        # Single character
        if len(text) == 1:
            if text in self.CHAR_TO_KEY:
                return self.CHAR_TO_KEY[text]
        
        # KEY_ name
        return self._key_name_to_code(text)
    
    def _parse_output(self, output_def) -> OutputAction:
        """Parse output definition"""
        # String output
        if isinstance(output_def, str):
            # Validate string length to prevent DoS
            MAX_OUTPUT_LENGTH = 10000  # 10KB limit
            if len(output_def) > MAX_OUTPUT_LENGTH:
                raise ValueError(f"Output string too long ({len(output_def)} chars), max is {MAX_OUTPUT_LENGTH}")
            return OutputAction(action_type='string', data=output_def)
        
        # List/sequence output
        if isinstance(output_def, list):
            # Limit sequence depth to prevent stack overflow
            MAX_SEQUENCE_DEPTH = 10
            if len(output_def) > MAX_SEQUENCE_DEPTH:
                raise ValueError(f"Sequence too deep ({len(output_def)} items), max is {MAX_SEQUENCE_DEPTH}")
            
            actions = []
            for item in output_def:
                if isinstance(item, str):
                    # Check if it's a KEY_ name or string
                    if item.startswith('KEY_'):
                        actions.append(OutputAction(action_type='key', data=self._key_name_to_code(item)))
                    else:
                        actions.append(OutputAction(action_type='string', data=item))
                elif isinstance(item, dict):
                    actions.append(self._parse_output(item))
            return OutputAction(action_type='sequence', data=actions)
        
        # Dict with key/modifiers
        if isinstance(output_def, dict):
            if 'key' in output_def:
                key_code = self._key_name_to_code(output_def['key'])
                modifiers = [self._key_name_to_code(m) for m in output_def.get('modifiers', [])]
                return OutputAction(action_type='key', data={'key': key_code, 'modifiers': modifiers})
            elif 'string' in output_def:
                return OutputAction(action_type='string', data=output_def['string'])
        
        raise ValueError(f"Invalid output definition: {output_def}")
    
    def _key_name_to_code(self, key_name: str) -> int:
        """Convert key name to evdev key code"""
        if isinstance(key_name, int):
            return key_name
        
        # Handle KEY_ prefix
        if not key_name.startswith('KEY_'):
            key_name = f'KEY_{key_name.upper()}'
        
        try:
            return getattr(e, key_name)
        except AttributeError:
            raise ValueError(f"Unknown key name: {key_name}")


class UmlautDaemon:
    """Main daemon that intercepts and remaps keyboard events"""
    
    def __init__(self):
        self.config = UmlautConfig()
        self.timeout_ms = self.config.timeout_ms
        self.timeout_sec = self.timeout_ms / 1000.0
        
        self.running = False
        self.devices = []
        self.uinput = None
        self.xdotool_available = False
        self._inotify_fd = None   # inotify fd for USB hotplug detection
        self._inotify_wd = None
        
        # Precompute set of valid compose keys for O(1) lookup
        # Extract compose keys from sequence tuples: (modifier, shift_flag, compose, ...)
        self.valid_compose_keys = set()
        for sequence_key in self.config.sequences.keys():
            # sequence_key is a tuple: (modifier, shift_flag, compose_key, ...)
            if len(sequence_key) >= 3:
                compose_key = sequence_key[2]  # Third element is compose key
                self.valid_compose_keys.add(compose_key)
        
        # State machine: IDLE -> MODIFIER_PRESSED -> COMPOSE_PRESSED -> WAITING_TARGET
        self.state = 'IDLE'
        self.pressed_keys = set()  # Currently pressed physical keys
        
        # Compose sequence tracking
        self.current_trigger = None  # Which modifier key was pressed
        self.current_compose = None   # Which compose key was pressed
        self.compose_shifted = False  # Was shift held when compose was pressed
        self.compose_start_time = 0
        self.trigger_start_time = 0   # When compose became active
        self.trigger_start_time = 0  # When modifier was first pressed
        
    def find_keyboard_devices(self) -> List[evdev.InputDevice]:
        """Find all keyboard input devices, excluding mice, touchpads, and media controls"""
        # Keys that define a real keyboard - must have majority of these
        REAL_KEYBOARD_KEYS = {
            e.KEY_A, e.KEY_B, e.KEY_C, e.KEY_D, e.KEY_E,
            e.KEY_SPACE, e.KEY_ENTER, e.KEY_BACKSPACE,
            e.KEY_LEFTSHIFT, e.KEY_LEFTCTRL
        }
        MIN_KEYBOARD_KEYS = 8  # Must match at least 8 of the above

        devices = []
        for device_path in evdev.list_devices():
            device = evdev.InputDevice(device_path)

            # Never grab our own virtual keyboard
            if device.name == 'umlaut-virtual-keyboard':
                continue

            # Check if device has key events at all
            caps = device.capabilities()
            if e.EV_KEY not in caps:
                continue

            # Skip if device has relative axes (mouse/touchpad)
            if e.EV_REL in caps:
                logger.debug(f"Skipping {device.name} (has EV_REL - likely mouse/touchpad)")
                continue

            # Skip if device has absolute axes but no keyboard keys (touchscreen/touchpad/tablet)
            if e.EV_ABS in caps:
                abs_axes = caps[e.EV_ABS]
                # Check for mouse/touchpad absolute positioning
                if e.ABS_X in abs_axes or e.ABS_Y in abs_axes:
                    logger.debug(f"Skipping {device.name} (has ABS_X/ABS_Y - touchscreen/tablet)")
                    continue
                # Check for multitouch (touchscreen/touchpad)
                if e.ABS_MT_POSITION_X in abs_axes:
                    logger.debug(f"Skipping {device.name} (has multitouch - touchscreen/touchpad)")
                    continue
                # If has ABS but it's for other purposes (joystick/gamepad), check for gamepad buttons
                keys = set(caps[e.EV_KEY])
                gamepad_buttons = {e.BTN_GAMEPAD, e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST, 
                                  e.BTN_A, e.BTN_B, e.BTN_X, e.BTN_Y}
                if keys & gamepad_buttons:
                    logger.debug(f"Skipping {device.name} (has gamepad buttons)")
                    continue
            
            # Skip if device has mouse buttons
            if e.EV_KEY in caps:
                keys = set(caps[e.EV_KEY])
                mouse_buttons = {e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_MOUSE}
                if keys & mouse_buttons:
                    logger.debug(f"Skipping {device.name} (has mouse buttons)")
                    continue

            # Check if device has enough real keyboard keys
            # This filters out BT headsets, media remotes, game controllers etc.
            keys = set(caps[e.EV_KEY])
            keyboard_matches = len(keys & REAL_KEYBOARD_KEYS)
            if keyboard_matches < MIN_KEYBOARD_KEYS:
                logger.debug(f"Skipping {device.name} (only {keyboard_matches}/{MIN_KEYBOARD_KEYS} keyboard keys - likely media/BT device)")
                continue

            devices.append(device)
            logger.info(f"Found keyboard: {device.name} at {device_path}")

        return devices
    
    def setup_uinput(self):
        """Create virtual keyboard for output"""
        # Get all key capabilities from original devices
        all_keys = set()
        for device in self.devices:
            caps = device.capabilities()
            if e.EV_KEY in caps:
                all_keys.update(caps[e.EV_KEY])
        
        # Create virtual keyboard with same capabilities
        # EV_LED is required for CapsLock/NumLock toggle state to work correctly
        self.uinput = UInput(
            events={
                e.EV_KEY: list(all_keys),
                e.EV_LED: [e.LED_CAPSL, e.LED_NUML, e.LED_SCROLLL],
            },
            name='umlaut-virtual-keyboard'
        )
        logger.info("Created virtual keyboard device")
    
    def grab_devices(self):
        """Exclusively grab keyboard devices"""
        for device in self.devices:
            try:
                device.grab()
                logger.info(f"Grabbed device: {device.name}")
            except IOError as ex:
                logger.error(f"Failed to grab {device.name}: {ex}")
                raise
    
    def ungrab_devices(self):
        """Release keyboard devices"""
        for device in self.devices:
            try:
                device.ungrab()
                logger.info(f"Released device: {device.name}")
            except Exception as ex:
                logger.warning(f"Error releasing {device.name}: {ex}")
    
    def emit_key(self, key_code: int, value: int, modifiers: List[int] = None):
        """Emit a key event with optional modifiers"""
        if modifiers:
            # Press modifiers
            for mod in modifiers:
                self.uinput.write(e.EV_KEY, mod, 1)
                self.uinput.syn()
        
        # Press/release the key
        self.uinput.write(e.EV_KEY, key_code, value)
        self.uinput.syn()
        
        if modifiers and value == 0:  # Release modifiers on key release
            for mod in modifiers:
                self.uinput.write(e.EV_KEY, mod, 0)
                self.uinput.syn()
    
    def emit_string(self, text: str):
        """Type a string of characters, handling shift for uppercase and Unicode"""
        for char in text:
            # Check if character is ASCII and in our character map
            if ord(char) <= 127 and (char in self.config.CHAR_TO_KEY or char in self.config.SHIFTED_CHARS):
                # Use direct key emission for ASCII characters
                needs_shift = False
                base_char = char
                
                if char in self.config.SHIFTED_CHARS:
                    base_char, needs_shift = self.config.SHIFTED_CHARS[char]
                
                # Get key code
                if base_char in self.config.CHAR_TO_KEY:
                    key_code = self.config.CHAR_TO_KEY[base_char]
                    
                    if needs_shift:
                        self.uinput.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
                        self.uinput.syn()
                    
                    # Press and release key
                    self.uinput.write(e.EV_KEY, key_code, 1)
                    self.uinput.syn()
                    self.uinput.write(e.EV_KEY, key_code, 0)
                    self.uinput.syn()
                    
                    if needs_shift:
                        self.uinput.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
                        self.uinput.syn()
                else:
                    logger.warning(f"Cannot type character: {char}")
            else:
                # Unicode character - use Ctrl+Shift+U method
                self.emit_unicode_char(char)
    
    def emit_unicode_char(self, char: str):
        """Emit a Unicode character using xdotool
        
        This works on X11 systems with xdotool installed.
        For uppercase characters, holds shift before typing.
        """
        # Security: Validate char is a single character
        if len(char) != 1:
            logger.error(f"emit_unicode_char requires single character, got: {repr(char)}")
            return
        
        if not self.xdotool_available:
            logger.warning(f"xdotool unavailable — cannot type Unicode character: {char!r}")
            return

        env = os.environ.copy()
        logger.debug(f"xdotool type: {char!r}")
        try:
            if char.isupper():
                subprocess.run(['xdotool', 'keydown', 'shift'],
                               check=True, capture_output=True, timeout=1, env=env)
                subprocess.run(['xdotool', 'type', '--', char],
                               check=True, capture_output=True, timeout=1, env=env)
                subprocess.run(['xdotool', 'keyup', 'shift'],
                               check=True, capture_output=True, timeout=1, env=env)
            else:
                subprocess.run(['xdotool', 'type', '--', char],
                               check=True, capture_output=True, timeout=1, env=env)
            logger.debug(f"xdotool success: {char!r}")
        except subprocess.TimeoutExpired:
            logger.error(f"xdotool timeout typing: {char!r}")
        except subprocess.CalledProcessError as ex:
            logger.error(f"xdotool failed for {char!r}: {ex.stderr}")
        except FileNotFoundError:
            logger.warning("xdotool not found — disabling Unicode output")
            self.xdotool_available = False
        self._inotify_fd = None   # inotify fd for USB hotplug detection
        self._inotify_wd = None
    
    def emit_output(self, output: OutputAction, target_was_shifted: bool = False):
        """Emit output based on action type
        
        Args:
            output: The output action to emit
            target_was_shifted: True if the user pressed Shift+key for the target
        """
        logger.debug(f"emit_output: type={output.action_type}, shifted={target_was_shifted}, data={output.data}")
        
        if output.action_type == 'string':
            # Apply uppercase if target was shifted
            text = output.data
            if target_was_shifted:
                text = text.upper()
                logger.debug(f"Uppercasing string: '{output.data}' → '{text}'")
            self.emit_string(text)
        
        elif output.action_type == 'key':
            if isinstance(output.data, dict):
                # Key with modifiers
                key_code = output.data['key']
                modifiers = list(output.data.get('modifiers', []))
                
                # Auto-add SHIFT if target was shifted and SHIFT not already present
                if target_was_shifted:
                    shift_keys = {e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT}
                    if not any(mod in shift_keys for mod in modifiers):
                        modifiers.append(e.KEY_LEFTSHIFT)
                
                self.emit_key(key_code, 1, modifiers)
                self.emit_key(key_code, 0, modifiers)
            else:
                # Just a key code
                modifiers = [e.KEY_LEFTSHIFT] if target_was_shifted else []
                self.emit_key(output.data, 1, modifiers)
                self.emit_key(output.data, 0, modifiers)
        
        elif output.action_type == 'sequence':
            # Sequence of actions - only apply shift to first action
            for i, action in enumerate(output.data):
                self.emit_output(action, target_was_shifted if i == 0 else False)
    
    def cancel_compose(self):
        """Cancel current compose sequence and reset to IDLE"""
        logger.debug("Compose sequence cancelled")
        self.state = 'IDLE'
        self.current_trigger = None
        self.current_compose = None
        self.compose_shifted = False
        self.compose_start_time = 0
        self.trigger_start_time = 0
    
    def force_release_all(self):
        """Force release all modifier and compose keys - emergency unstick"""
        logger.debug("Force releasing all compose keys")
        if self.current_trigger:
            self.uinput.write(e.EV_KEY, self.current_trigger, 0)
            self.uinput.syn()
        if self.current_compose:
            self.uinput.write(e.EV_KEY, self.current_compose, 0)
            self.uinput.syn()
        #Release shift if it was part of compose
        self.uinput.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
        self.uinput.syn()
        self.uinput.write(e.EV_KEY, e.KEY_RIGHTSHIFT, 0)
        self.uinput.syn()
        self.cancel_compose()
    
    def check_timeout(self):
        """Check if modifier or compose sequence has timed out"""
        now = time.time()

        if self.state == 'TRIGGER_PRESSED':
            if now - self.trigger_start_time >= self.timeout_sec:
                logger.debug("Modifier timeout - passing through")
                self.uinput.write(e.EV_KEY, self.current_trigger, 1)
                self.uinput.syn()
                self.uinput.write(e.EV_KEY, self.current_trigger, 0)
                self.uinput.syn()
                self.cancel_compose()

        elif self.state == 'WAITING_TARGET':
            if now - self.compose_start_time >= self.timeout_sec:
                logger.debug("Compose timeout - passing through original keys")
                # Send modifier press/release
                self.uinput.write(e.EV_KEY, self.current_trigger, 1)
                self.uinput.syn()
                self.uinput.write(e.EV_KEY, self.current_trigger, 0)
                self.uinput.syn()
                # Send compose press/release (with shift if needed)
                if self.compose_shifted:
                    self.uinput.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
                    self.uinput.syn()
                self.uinput.write(e.EV_KEY, self.current_compose, 1)
                self.uinput.syn()
                self.uinput.write(e.EV_KEY, self.current_compose, 0)
                self.uinput.syn()
                if self.compose_shifted:
                    self.uinput.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
                    self.uinput.syn()
                self.cancel_compose()
    
    def handle_event(self, event):
        """Process a keyboard event"""
        if event.type != e.EV_KEY:
            # Pass through non-key events
            self.uinput.write(event.type, event.code, event.value)
            return
        
        key_code = event.code
        value = event.value  # 1 = press, 0 = release, 2 = repeat
        
        # Update pressed keys state
        if value == 1:  # Key press
            self.pressed_keys.add(key_code)
        elif value == 0:  # Key release
            self.pressed_keys.discard(key_code)
        
        # Ignore key repeat events during compose
        if value == 2 and self.state != 'IDLE':
            return
        
        # ESC always cancels and force-releases everything
        if key_code == e.KEY_ESC and value == 1:
            if self.state != 'IDLE':
                logger.debug("ESC pressed - force cancelling compose")
                self.force_release_all()
                return  # Don't pass through ESC if we cancelled
        
        # State machine
        if self.state == 'IDLE':
            # Check if this is a modifier key press
            if value == 1 and key_code in self.config.trigger_keys_list:
                # Check if other modifiers are already pressed
                # Shift is OK (can be part of SHIFT+KEY compose sequences)
                # But Ctrl/Meta indicate a regular shortcut
                other_modifiers = {e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL,
                                  e.KEY_LEFTMETA, e.KEY_RIGHTMETA}
                other_modifiers.discard(key_code)  # Remove current modifier
                
                if self.pressed_keys & other_modifiers:
                    # Ctrl or Meta already pressed - this is a regular shortcut
                    logger.debug(f"Modifier {key_code} with other mods - passing through")
                    self.uinput.write(e.EV_KEY, key_code, value)
                    return
                
                self.current_trigger = key_code
                self.state = 'TRIGGER_PRESSED'
                self.trigger_start_time = time.time()
                logger.debug(f"Modifier pressed: {key_code}")
                return  # Don't pass through yet
            
        elif self.state == 'TRIGGER_PRESSED':
            # Check if modifier released before compose key
            if value == 0 and key_code == self.current_trigger:
                logger.debug("Modifier released early - passing through")
                # Send modifier press and release
                self.uinput.write(e.EV_KEY, self.current_trigger, 1)
                self.uinput.syn()
                self.uinput.write(e.EV_KEY, self.current_trigger, 0)
                self.uinput.syn()
                self.cancel_compose()
                return
            
            # Check if another modifier key is pressed (e.g., Alt then Ctrl)
            # BUT: Don't abort if Shift+NextKey could be a valid compose sequence
            if value == 1 and key_code in (e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL,
                                           e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT,
                                           e.KEY_LEFTMETA, e.KEY_RIGHTMETA):
                if key_code not in self.config.trigger_keys_list:
                    # Check if this might be part of a SHIFT+KEY compose sequence
                    if key_code in (e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT):
                        # Don't abort yet - wait to see if next key is a valid compose key
                        logger.debug(f"Shift pressed during modifier - waiting for compose key")
                        return  # Continue in MODIFIER_PRESSED state
                    
                    # For other modifiers (Ctrl, Meta), this is definitely a shortcut
                    logger.debug(f"Additional modifier {key_code} pressed - passing through")
                    # Send original modifier, then this key
                    self.uinput.write(e.EV_KEY, self.current_trigger, 1)
                    self.uinput.syn()
                    self.uinput.write(e.EV_KEY, key_code, value)
                    self.uinput.syn()
                    self.cancel_compose()
                    return
            
            # Check if this is an ignored key (e.g., TAB for Alt-Tab)
            if value == 1 and key_code in self.config.passthrough_keys:
                logger.debug(f"Ignored key {key_code} - passing through")
                # Send modifier and ignored key
                self.uinput.write(e.EV_KEY, self.current_trigger, 1)
                self.uinput.syn()
                self.uinput.write(e.EV_KEY, key_code, value)
                self.uinput.syn()
                self.cancel_compose()
                return
            
            # Check if this is a compose key press (while modifier held)
            if value == 1 and key_code != self.current_trigger:
                # Only enter compose mode if this key has defined sequences
                if key_code not in self.valid_compose_keys:
                    logger.debug(f"Key {key_code} has no sequences - passing through as shortcut")
                    # Send modifier and key as regular shortcut
                    self.uinput.write(e.EV_KEY, self.current_trigger, 1)
                    self.uinput.syn()
                    self.uinput.write(e.EV_KEY, key_code, value)
                    self.uinput.syn()
                    self.cancel_compose()
                    return
                
                self.current_compose = key_code
                # Check if shift is pressed
                self.compose_shifted = (e.KEY_LEFTSHIFT in self.pressed_keys or 
                                       e.KEY_RIGHTSHIFT in self.pressed_keys)
                self.state = 'COMPOSE_PRESSED'
                logger.debug(f"Compose key pressed: {key_code} (shifted={self.compose_shifted})")
                return  # Don't pass through yet
            
        elif self.state == 'COMPOSE_PRESSED':
            # Ignore modifier key presses/releases (user might hold shift through the sequence)
            if key_code in (e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT, 
                           e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL,
                           e.KEY_LEFTALT, e.KEY_RIGHTALT,
                           e.KEY_LEFTMETA, e.KEY_RIGHTMETA):
                # Pass Shift releases through so X11 doesn't think Shift is still held
                if value == 0 and key_code in (e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT):
                    self.uinput.write(e.EV_KEY, key_code, 0)
                    self.uinput.syn()
            
            # Waiting for modifier and compose to be released
            if value == 0:
                if key_code == self.current_trigger or key_code == self.current_compose:
                    modifier_released = self.current_trigger not in self.pressed_keys
                    compose_released = self.current_compose not in self.pressed_keys
                    
                    if modifier_released and compose_released:
                        # Both released - now waiting for target
                        self.state = 'WAITING_TARGET'
                        self.compose_start_time = time.time()
                        logger.debug("Waiting for target key")
                        return
                    # Partial release - keep waiting
                    return
            
        elif self.state == 'WAITING_TARGET':
            # Ignore modifier key presses/releases while waiting for target
            # (user might press Shift+A, we only care about the A)
            if key_code in (e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT, 
                           e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL,
                           e.KEY_LEFTALT, e.KEY_RIGHTALT,
                           e.KEY_LEFTMETA, e.KEY_RIGHTMETA):
                # Pass Shift releases through so X11 doesn't think Shift is still held
                if value == 0 and key_code in (e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT):
                    self.uinput.write(e.EV_KEY, key_code, 0)
                    self.uinput.syn()
                return  # Stay in WAITING_TARGET
            
            # Waiting for target key press
            if value == 1:  # Key press
                # Build target keys
                target_keys = [key_code]
                target_was_shifted = False
                
                logger.debug(f"pressed_keys at target time: {self.pressed_keys}")
                # Check modifiers on target
                if e.KEY_LEFTSHIFT in self.pressed_keys or e.KEY_RIGHTSHIFT in self.pressed_keys:
                    target_keys.insert(0, e.KEY_LEFTSHIFT)
                    target_was_shifted = True
                if e.KEY_LEFTCTRL in self.pressed_keys or e.KEY_RIGHTCTRL in self.pressed_keys:
                    target_keys.insert(0, e.KEY_LEFTCTRL)
                if e.KEY_LEFTALT in self.pressed_keys or e.KEY_RIGHTALT in self.pressed_keys:
                    if key_code not in self.config.trigger_keys_list:
                        target_keys.insert(0, e.KEY_LEFTALT)
                
                # Build lookup key with compose_shifted flag
                lookup_key = (self.current_trigger, self.compose_shifted, self.current_compose, *target_keys)
                
                logger.debug(f"Looking up: {lookup_key}")
                
                # Try to find match
                matched_seq = None
                if lookup_key in self.config.sequences:
                    matched_seq = self.config.sequences[lookup_key]
                elif target_was_shifted:
                    # If target was shifted, also try without shift in lookup
                    # This allows "u" config to match both "u" and "U" (Shift+U)
                    unshifted_target_keys = [k for k in target_keys if k not in (e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT)]
                    unshifted_lookup_key = (self.current_trigger, self.compose_shifted, self.current_compose, *unshifted_target_keys)
                    logger.debug(f"Also trying unshifted lookup: {unshifted_lookup_key}")
                    if unshifted_lookup_key in self.config.sequences:
                        matched_seq = self.config.sequences[unshifted_lookup_key]
                
                if matched_seq:
                    # Match found!
                    logger.debug(f"Sequence matched")

                    # Emit output
                    self.emit_output(matched_seq.output, target_was_shifted)
                    
                    self.cancel_compose()
                    return  # Event handled, don't pass through
                else:
                    # No match - pass through modifier, compose, then target
                    logger.debug(f"No match for {lookup_key} - passing through")
                    
                    # Send modifier press and release
                    self.uinput.write(e.EV_KEY, self.current_trigger, 1)
                    self.uinput.syn()
                    self.uinput.write(e.EV_KEY, self.current_trigger, 0)
                    self.uinput.syn()
                    
                    # Send compose press and release (with shift if needed)
                    if self.compose_shifted:
                        self.uinput.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
                        self.uinput.syn()
                    self.uinput.write(e.EV_KEY, self.current_compose, 1)
                    self.uinput.syn()
                    self.uinput.write(e.EV_KEY, self.current_compose, 0)
                    self.uinput.syn()
                    if self.compose_shifted:
                        self.uinput.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
                        self.uinput.syn()
                    
                    # Now send the target key
                    self.uinput.write(e.EV_KEY, key_code, value)
                    self.uinput.syn()
                    
                    self.cancel_compose()
                    return
        
        # Pass through if not handled
        self.uinput.write(e.EV_KEY, key_code, value)
        self.uinput.syn()
    
    def _check_xdotool(self):
        """Verify xdotool is available and working. Sets self.xdotool_available flag."""
        self.xdotool_available = False
        self._inotify_fd = None   # inotify fd for USB hotplug detection
        self._inotify_wd = None

        # Check for Wayland first
        if (os.environ.get('XDG_SESSION_TYPE') == 'wayland'
                or os.environ.get('WAYLAND_DISPLAY')):
            logger.warning("Wayland session detected — Unicode output via xdotool disabled")
            return

        # Check DISPLAY is set
        if 'DISPLAY' not in os.environ:
            logger.warning("DISPLAY not set — Unicode output via xdotool disabled")
            return

        # Verify xdotool binary exists and responds
        try:
            result = subprocess.run(
                ['xdotool', 'version'],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                self.xdotool_available = True
                logger.info(f"xdotool ready: {result.stdout.strip().splitlines()[0]}")
            else:
                logger.warning("xdotool returned non-zero — Unicode output disabled")
        except FileNotFoundError:
            logger.warning("xdotool not found — install with: sudo apt install xdotool")
        except subprocess.TimeoutExpired:
            logger.warning("xdotool timed out during check — Unicode output disabled")

    def _setup_inotify(self):
        """Set up inotify watch on /dev/input for new keyboard detection."""
        try:
            libc = ctypes.CDLL('libc.so.6', use_errno=True)
            fd = libc.inotify_init1(0o0004000)  # IN_NONBLOCK
            if fd < 0:
                raise OSError(ctypes.get_errno(), "inotify_init1 failed")
            IN_CREATE = 0x00000100
            wd = libc.inotify_add_watch(fd, b'/dev/input', IN_CREATE)
            if wd < 0:
                raise OSError(ctypes.get_errno(), "inotify_add_watch failed")
            self._inotify_fd = fd
            self._inotify_wd = wd
            logger.info("inotify watchdog active on /dev/input")
        except Exception as ex:
            logger.warning(f"inotify unavailable — USB hotplug detection disabled: {ex}")
            self._inotify_fd = None

    def _drain_inotify(self) -> list:
        """Read pending inotify events, return list of new device paths."""
        if self._inotify_fd is None:
            return []
        new_paths = []
        # Each inotify event: 16 bytes header + name (len in header)
        EVENT_HEADER = struct.Struct('iIII')  # wd, mask, cookie, len
        try:
            data = os.read(self._inotify_fd, 4096)
            offset = 0
            while offset < len(data):
                if offset + EVENT_HEADER.size > len(data):
                    break
                wd, mask, cookie, name_len = EVENT_HEADER.unpack_from(data, offset)
                offset += EVENT_HEADER.size
                if name_len > 0:
                    name = data[offset:offset + name_len].rstrip(b'\x00').decode('utf-8', errors='ignore')
                    offset += name_len
                    if name.startswith('event'):
                        new_paths.append(f'/dev/input/{name}')
                else:
                    offset += name_len
        except BlockingIOError:
            pass  # No events pending
        except Exception as ex:
            logger.debug(f"inotify read error: {ex}")
        return new_paths

    def _check_new_devices(self, device_map: dict):
        """Check inotify for new keyboards and grab them."""
        new_paths = self._drain_inotify()
        for path in new_paths:
            if path in {dev.path for dev in self.devices}:
                continue
            try:
                import time as _time
                _time.sleep(0.3)  # Let udev settle
                dev = evdev.InputDevice(path)
                if dev.name == 'umlaut-virtual-keyboard':
                    dev.close()
                    continue
                caps = dev.capabilities()
                REAL_KEYS = {e.KEY_A, e.KEY_SPACE, e.KEY_ENTER,
                             e.KEY_BACKSPACE, e.KEY_LEFTSHIFT, e.KEY_LEFTCTRL}
                keys = set(caps.get(e.EV_KEY, []))
                if e.EV_KEY in caps and len(keys & REAL_KEYS) >= 4:
                    dev.grab()
                    self.devices.append(dev)
                    device_map[dev.fd] = dev
                    logger.info(f"Hotplug: grabbed new keyboard {dev.name} at {path}")
                else:
                    dev.close()
            except Exception as ex:
                logger.debug(f"Hotplug: could not open {path}: {ex}")

    def run(self):
        """Main event loop"""
        self.running = True
        
        logger.info("Umlaut daemon starting...")
        
        # Find and grab devices
        self.devices = self.find_keyboard_devices()
        if not self.devices:
            logger.error("No keyboard devices found!")
            return 1
        
        self.setup_uinput()
        self.grab_devices()
        self._setup_inotify()
        self._check_xdotool()
        
        logger.info("Umlaut daemon ready. Press Ctrl+C to stop.")
        
        try:
            # Create device map for select
            device_map = {dev.fd: dev for dev in self.devices}
            
            # Add inotify fd to watched fds if available
            watch_fds = set(device_map.keys())
            if self._inotify_fd is not None:
                watch_fds.add(self._inotify_fd)

            while self.running:
                # Check timeout
                self.check_timeout()

                # Dynamic select timeout: wait only until next deadline
                now = time.time()
                if self.state == 'TRIGGER_PRESSED':
                    deadline = self.trigger_start_time + self.timeout_sec
                elif self.state == 'WAITING_TARGET':
                    deadline = self.compose_start_time + self.timeout_sec
                else:
                    deadline = now + 1.0  # IDLE: wake up at most every 1s
                select_timeout = max(0.005, deadline - now)
                r, w, x = select.select(watch_fds, [], [], select_timeout)

                # Handle inotify events (new USB keyboard plugged in)
                if self._inotify_fd in r:
                    self._check_new_devices(device_map)
                    watch_fds = set(device_map.keys())
                    if self._inotify_fd is not None:
                        watch_fds.add(self._inotify_fd)

                for fd in r:
                    if fd == self._inotify_fd:
                        continue
                    device = device_map[fd]
                    try:
                        for event in device.read():
                            self.handle_event(event)
                            if event.type == e.EV_SYN:
                                self.uinput.syn()
                    except OSError:
                        # Device disconnected - remove from map to prevent infinite loop
                        logger.warning(f"Device {device.name} disconnected - removing from monitoring")
                        del device_map[fd]
                        self.devices.remove(device)
                        watch_fds.discard(fd)
                        continue
        
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self.cleanup()
        
        return 0
    
    def cleanup(self):
        """Clean up resources"""
        self.running = False
        self.ungrab_devices()
        
        if self.uinput:
            self.uinput.close()
        
        for device in self.devices:
            device.close()
        
        if self._inotify_fd is not None:
            try:
                os.close(self._inotify_fd)
            except Exception:
                pass
        logger.info("Cleanup complete")
    
    def reload_config(self):
        """Reload configuration file"""
        logger.info("Reloading configuration...")
        try:
            self.config.load_config()
            # Reset state — force-release any stuck keys
            self.force_release_all()
            logger.info("Configuration reloaded successfully")
        except Exception as e:
            logger.error(f"Failed to reload config: {e}")


_daemon_instance = None

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}")
    if _daemon_instance is not None:
        try:
            _daemon_instance.force_release_all()
        except Exception:
            pass
    sys.exit(0)


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Umlaut Daemon - System-wide keyboard remapping',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--debug', '-d',
                       action='store_true',
                       help='Enable debug logging')
    
    args = parser.parse_args()
    
    # Configure logging based on debug flag
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    logger.info(f"Starting Umlaut daemon (debug={'ON' if args.debug else 'OFF'})")
    
    # Setup signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    global _daemon_instance
    daemon = UmlautDaemon()
    _daemon_instance = daemon
    return daemon.run()


if __name__ == '__main__':
    sys.exit(main())
