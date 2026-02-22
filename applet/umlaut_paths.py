#!/usr/bin/env python3
"""
Umlaut shared path constants, config schemas, and I/O helpers.
Import this in all umlaut scripts to avoid hardcoded path duplication.
"""

from pathlib import Path
import json
import logging

logger = logging.getLogger('umlaut')

# ── Installation paths ────────────────────────────────────────────────────
INSTALL_DIR       = Path('/usr/local/bin/umlaut-scripts')
UMLAUT_CTL        = Path('/usr/local/bin/umlaut')

# ── Icon paths ────────────────────────────────────────────────────────────
SYSTEM_ICON_DIR   = Path('/usr/share/pixmaps/umlaut')

# ── User paths ────────────────────────────────────────────────────────────
USER_CONFIG_DIR   = Path.home() / '.config' / 'umlaut'
USER_ICON_DIR     = USER_CONFIG_DIR / 'icons'
USER_SETTINGS     = USER_CONFIG_DIR / 'settings.config.json'

# ── Scripts ───────────────────────────────────────────────────────────────
APPLET_SCRIPT     = INSTALL_DIR / 'umlaut_applet.py'
CONFIG_MGR_SCRIPT = INSTALL_DIR / 'umlaut_config_manager.py'
DAEMON_SCRIPT     = INSTALL_DIR / 'umlaut_daemon.py'

# ── Settings schema ───────────────────────────────────────────────────────
SETTINGS_SCHEMA = {
    'version':           1,
    'trigger_key':       ['KEY_LEFTALT', 'KEY_RIGHTALT'],
    'passthrough_keys':  [],
    'enabled_sequences': [],
    'icon_set':          'default',
    'settings': {
        'timeout_ms': 1000,
        'log_level':  'INFO',
    },
}

# ── Sequence config schema ────────────────────────────────────────────────
SEQUENCE_SCHEMA = {
    'version':     1,
    'name':        '',
    'description': '',
    'sequences':   {},
}


def _apply_schema(data: dict, schema: dict) -> dict:
    """Return a cleaned dict: only schema keys kept, missing keys filled with defaults.
    Nested dicts handled one level deep."""
    out = {}
    for key, default in schema.items():
        val = data.get(key, default)
        if isinstance(default, dict) and isinstance(val, dict):
            out[key] = {k: val.get(k, v) for k, v in default.items()}
        else:
            out[key] = val
    return out


def load_settings() -> dict:
    """Load and clean settings.config.json against SETTINGS_SCHEMA.
    Returns guaranteed-shape dict. Unknown keys dropped, missing filled with defaults."""
    raw = {}
    if USER_SETTINGS.exists():
        try:
            with open(USER_SETTINGS) as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raw = {}
        except Exception:
            raw = {}
    return _apply_schema(raw, SETTINGS_SCHEMA)


def save_settings(data: dict) -> None:
    """Save settings, stripping unknown keys before writing."""
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cleaned = _apply_schema(data, SETTINGS_SCHEMA)
    with open(USER_SETTINGS, 'w') as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)


def load_sequence_config(path) -> dict | None:
    """Load and clean a sequence config file.
    Unknown top-level keys dropped. Malformed entries dropped at smallest unit.
    Returns None if file is missing or not valid JSON."""
    try:
        with open(path) as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            logger.debug(f"Sequence config {path}: not a JSON object")
            return None
    except FileNotFoundError:
        logger.debug(f"Sequence config {path}: not found")
        return None
    except json.JSONDecodeError as e:
        logger.debug(f"Sequence config {path}: invalid JSON — {e}")
        return None

    cleaned = _apply_schema(raw, SEQUENCE_SCHEMA)

    raw_seqs = raw.get('sequences', {})
    if not isinstance(raw_seqs, dict):
        logger.debug(f"Sequence config {path}: 'sequences' not a dict, resetting")
        cleaned['sequences'] = {}
        return cleaned

    clean_seqs = {}
    aliases = {}

    for compose_key, targets in raw_seqs.items():
        if isinstance(targets, str):
            aliases[compose_key] = targets
            continue
        if not isinstance(targets, dict):
            logger.debug(f"{path}: dropping '{compose_key}' — expected dict or alias, got {type(targets).__name__}")
            continue
        clean_targets = {}
        for target, output in targets.items():
            if not isinstance(output, str):
                logger.debug(f"{path}: dropping '{compose_key}+{target}' — output must be string")
                continue
            clean_targets[target] = output
        if clean_targets:
            clean_seqs[compose_key] = clean_targets
        else:
            logger.debug(f"{path}: dropping '{compose_key}' — no valid targets")

    for compose_key, ref in aliases.items():
        if ref in clean_seqs:
            clean_seqs[compose_key] = ref
        else:
            logger.debug(f"{path}: dropping alias '{compose_key}' — references unknown '{ref}'")

    cleaned['sequences'] = clean_seqs
    return cleaned
