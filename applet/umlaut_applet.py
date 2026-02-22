#!/usr/bin/env python3
"""
Umlaut Tray Applet - Phase 1
GTK3 AppIndicator tray icon with daemon control
"""

import gi
gi.require_version('Gtk', '3.0')

# Support both Ayatana (newer) and legacy AppIndicator3
try:
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
except ValueError:
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import AppIndicator3

from gi.repository import Gtk, GLib
import subprocess
import os
import logging
import signal
from umlaut_paths import (
    APPLET_SCRIPT, USER_SETTINGS,
    UMLAUT_CTL, SYSTEM_ICON_DIR as _SYS_ICON_DIR,
    USER_ICON_DIR as _USR_ICON_DIR,
    USER_CONFIG_DIR as _USR_CFG_DIR,
)

logger = logging.getLogger('umlaut-applet')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Paths
UMLAUT_CONTROL = str(UMLAUT_CTL)
SYSTEM_ICON_DIR = str(_SYS_ICON_DIR)
USER_ICON_DIR   = str(_USR_ICON_DIR)
USER_CONFIG_DIR = str(_USR_CFG_DIR)

# Theme icon fallbacks
ICON_ACTIVE   = 'input-keyboard'
ICON_INACTIVE = 'input-keyboard-symbolic'


class UmlautApplet:
    def __init__(self):
        # Load icon set before first _get_icon call
        self._icon_set = 'default'
        self._reload_icon_set()

        self.indicator = AppIndicator3.Indicator.new(
            'umlaut',
            self._get_icon('inactive'),
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title('Umlaut')

        # Track state for error detection
        self.expected_running = None  # What we expect after last command
        self.config_error = False       # Persistent failed state
        self.config_error_msg = ''      # Error message from daemon
        
        # Track child processes (like config manager)
        self.manager_process = None  # Single config manager instance
        # On SIGTERM (external kill): quit cleanly but don't kill children —
        # config manager may have been opened intentionally and should survive.
        # Children are only killed on explicit Quit from the menu.
        signal.signal(signal.SIGTERM, lambda *_: GLib.idle_add(Gtk.main_quit))

        # Build menu
        self.menu = Gtk.Menu()
        self._build_menu()
        self.indicator.set_menu(self.menu)

        # Start status polling (every 3 seconds)
        GLib.timeout_add_seconds(3, self._poll_status)

        # Initial status check (do it directly, don't use idle_add with a function that returns True)
        self._force_poll_now()

    def _reload_icon_set(self):
        """Read icon_set from settings.config.json and cache it."""
        import json
        try:
            with open(os.path.join(USER_CONFIG_DIR, 'settings.config.json')) as f:
                self._icon_set = json.load(f).get('icon_set', 'default').lower()
        except Exception:
            self._icon_set = 'default'

    def _get_icon(self, state: str) -> str:
        """Get icon path for current icon_set and state.
        Falls back to 'default' icon set if named set not found.
        Falls back to theme icon name if file not found.
        """
        icon_set = self._icon_set
        for candidate in [icon_set, 'default']:
            filename = f'{candidate}.{state}.png'
            for directory in [USER_ICON_DIR, SYSTEM_ICON_DIR]:
                path = os.path.join(directory, filename)
                if os.path.exists(path):
                    return path
        # Fallbacks
        if state == 'error':
            return 'dialog-error'
        return ICON_ACTIVE if state == 'active' else ICON_INACTIVE


    def _build_menu(self):
        """Build the tray menu"""
        # Status item (non-clickable, shows current state)
        self.status_item = Gtk.MenuItem(label='Status: Checking...')
        self.status_item.set_sensitive(False)
        self.menu.append(self.status_item)

        # Separator
        self.menu.append(Gtk.SeparatorMenuItem())

        # Start
        self.start_item = Gtk.MenuItem(label='Start')
        self.start_item.connect('activate', self._on_start)
        self.menu.append(self.start_item)

        # Stop
        self.stop_item = Gtk.MenuItem(label='Stop')
        self.stop_item.connect('activate', self._on_stop)
        self.menu.append(self.stop_item)

        # Restart
        self.restart_item = Gtk.MenuItem(label='Restart')
        self.restart_item.connect('activate', self._on_restart)
        self.menu.append(self.restart_item)

        # Separator (hidden in failed state, shown otherwise)
        self.error_sep = Gtk.SeparatorMenuItem()
        self.menu.append(self.error_sep)

        # Configure
        self.configure_item = Gtk.MenuItem(label='Configure...')
        self.configure_item.connect('activate', self._on_configure)
        self.menu.append(self.configure_item)

        # Separator
        self.menu.append(Gtk.SeparatorMenuItem())

        # Quit
        quit_item = Gtk.MenuItem(label='Quit')
        quit_item.connect('activate', self._on_quit)
        self.menu.append(quit_item)

        self.menu.show_all()

    def _run_control(self, *args) -> tuple:
        """Run the umlaut control script (no sudo - user service)"""
        try:
            result = subprocess.run(
                [UMLAUT_CONTROL] + list(args),
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return False, '', 'Command timed out'
        except Exception as ex:
            return False, '', str(ex)

    def _get_daemon_state(self) -> str:
        """Returns 'active', 'failed', or 'inactive'."""
        try:
            result = subprocess.run(
                ['systemctl', '--user', 'is-active', 'umlaut'],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()
            if status == 'active':
                return 'active'
            elif status == 'failed':
                return 'failed'
            else:
                return 'inactive'
        except Exception:
            return 'inactive'

    def _poll_status(self) -> bool:
        """Poll daemon status. Returns False (stops polling) when in config_error state."""
        if self.config_error:
            return False
        self._update_status()
        return True

    def _force_poll_now(self):
        self._update_status()

    def _update_status(self):
        """Check daemon status and update UI accordingly."""
        state = self._get_daemon_state()

        if state == 'failed':
            if not self.config_error_msg:
                try:
                    result = subprocess.run(
                        ['journalctl', '--user', '-u', 'umlaut', '-n', '20',
                         '--no-pager', '-o', 'cat'],
                        capture_output=True, text=True, timeout=5
                    )
                    lines = result.stdout.strip().splitlines()
                    # Prefer ERROR lines; fall back to last non-empty line
                    errors = [l for l in lines if 'ERROR' in l or 'FATAL' in l]
                    msg = errors[-1] if errors else (lines[-1] if lines else '')
                    self.config_error_msg = msg if msg else 'Daemon failed to start'
                except Exception:
                    self.config_error_msg = 'Daemon failed to start'
            self.config_error = True
            self._update_ui('failed')
            return

        # If we had a config error, stay in failed UI until daemon actually
        # reaches active (guards against systemd restart cycling through inactive)
        if self.config_error and state != 'active':
            self._update_ui('failed')
            return

        # Daemon is active or cleanly inactive - clear error state
        self.config_error = False
        self.config_error_msg = ''

        if self.expected_running is True and state != 'active':
            self._update_ui('error')
            self.expected_running = None
        elif self.expected_running is False and state == 'active':
            self._update_ui('error')
            self.expected_running = None
        else:
            self._update_ui(state)

    def _update_ui(self, state: str):
        """Update icon and menu items based on daemon state."""
        # (label, icon_state, icon_label, start_vis, stop_vis, restart_vis,
        #  start_sens, stop_sens, restart_sens, seps_vis, error_sep_vis)
        _STATE = {
            'active':   ('🟢 Running',          'active', 'Umlaut Running',
                         True,  True,  True,  False, True,  True,  True,  False),
            'inactive': ('⚪ Stopped',           'inactive', 'Umlaut Stopped',
                         True,  True,  True,  True,  False, False, True,  False),
            'failed':   ('🔴 Failed to start',  'error',  'Umlaut Failed',
                         True,  False, False, True,  False, False, False, True),
            'error':    ('🔴 Error - check log','error',  'Umlaut Error',
                         True,  False, False, True,  False, False, True,  False),
        }
        (label, icon_state, icon_label,
         start_vis, stop_vis, restart_vis,
         start_sens, stop_sens, restart_sens,
         seps_vis, error_sep_vis) = _STATE.get(state, _STATE['inactive'])

        self.status_item.set_label(label)
        self.indicator.set_icon_full(self._get_icon(icon_state), icon_label)
        self.start_item.set_visible(start_vis)
        self.stop_item.set_visible(stop_vis)
        self.restart_item.set_visible(restart_vis)
        self.start_item.set_sensitive(start_sens)
        self.stop_item.set_sensitive(stop_sens)
        self.restart_item.set_sensitive(restart_sens)
        for child in self.menu.get_children():
            if isinstance(child, Gtk.SeparatorMenuItem):
                child.set_visible(seps_vis)
        self.error_sep.set_visible(error_sep_vis)
        self.menu.show_all()

    def _on_start(self, _):
        self.expected_running = True
        success, out, err = self._run_control('start')
        if not success:
            # Check if it's a config error (daemon wrote error file)
            self._force_poll_now()  # will set config_error if state is 'failed'
            if not self.config_error:
                self._show_error('Failed to start Umlaut', err)
            self.expected_running = None
        else:
            # Successful start — clear any previous error state, resume polling
            self.config_error = False
            self.config_error_msg = ''
            GLib.timeout_add_seconds(3, self._poll_status)
            self._force_poll_now()

    def _on_stop(self, _):
        self.expected_running = False
        success, out, err = self._run_control('stop')
        if not success:
            self._show_error('Failed to stop Umlaut', err)
            self.expected_running = None
        self._force_poll_now()

    def _on_restart(self, _):
        self.expected_running = True
        success, out, err = self._run_control('restart')
        if not success:
            self._force_poll_now()
            if not self.config_error:
                self._show_error('Failed to restart Umlaut', err)
            self.expected_running = None
        else:
            self.config_error = False
            self.config_error_msg = ''
            GLib.timeout_add_seconds(3, self._poll_status)
            self._force_poll_now()

    def _kill_manager(self):
        """Terminate config manager if running."""
        if self.manager_process and self.manager_process.poll() is None:
            try:
                self.manager_process.terminate()
                self.manager_process.wait(timeout=2)
            except Exception:
                pass
        self.manager_process = None

    def _on_configure(self, _):
        """Open config manager GUI — ensure only one instance runs."""
        # Kill any existing instance by script name (survives applet restarts)
        subprocess.run(['pkill', '-f', 'umlaut_config_manager.py'],
                       capture_output=True)
        try:
            self.manager_process = subprocess.Popen(
                ['/usr/local/bin/umlaut-scripts/umlaut_config_manager.py'])
        except Exception as e:
            logger.error(f"Failed to open config manager: {e}")

    def _on_quit(self, _):
        """Quit applet and close any child windows"""
        self._kill_manager()
        Gtk.main_quit()

    def _show_error(self, title: str, message: str):
        """Show an error dialog"""
        dialog = Gtk.MessageDialog(
            transient_for=None,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()


def _kill_existing_applet():
    """Kill any existing umlaut_applet process (except ourselves)"""
    import signal
    my_pid = os.getpid()
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'umlaut_applet'],
            capture_output=True, text=True
        )
        for pid_str in result.stdout.strip().splitlines():
            pid = int(pid_str)
            if pid != my_pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    logger.info(f"Killed existing applet process {pid}")
                except ProcessLookupError:
                    pass
    except Exception as e:
        logger.warning(f"Failed to kill existing applet: {e}")


def main():
    # Parse args
    import argparse
    parser = argparse.ArgumentParser(description='Umlaut System Tray Applet')
    parser.add_argument('--delay', type=int, default=0,
                        help='Delay in seconds before starting (for autostart)')
    args = parser.parse_args()

    # Kill any existing applet instance
    _kill_existing_applet()

    # Apply startup delay if requested
    if args.delay > 0:
        logger.info(f"Waiting {args.delay}s before starting...")
        import time
        time.sleep(args.delay)

    # Start applet
    applet = UmlautApplet()
    Gtk.main()


if __name__ == '__main__':
    main()
