#!/usr/bin/env python3
"""macOS menu bar launcher for the local transcriber.

Puts a microphone icon in the menu bar. Launching it starts the Flask app
(`.venv/bin/python app.py`) and opens it in your browser; the dropdown lets you
open the page, stop/start the server, view the log, and quit. While a meeting
is being recorded the icon shows a live timer.

Run directly:   .venv/bin/python menubar.py
Or build an app: ./make_app.sh   ->  ~/Applications/Transcriber.app
"""
import json
import os
import signal
import subprocess
import time
import urllib.request
import webbrowser

import logging
import sys

import rumps

ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(ROOT, ".venv", "bin", "python")
APP_PY = os.path.join(ROOT, "app.py")
PORT = int(os.environ.get("TRANSCRIBER_PORT", "5001"))
URL = f"http://localhost:{PORT}"
LOG_DIR = os.path.expanduser("~/Library/Logs/Transcriber")
LOG_PATH = os.path.join(LOG_DIR, "server.log")
MENUBAR_LOG = os.path.join(LOG_DIR, "menubar.log")

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=MENUBAR_LOG, level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
log = logging.getLogger("menubar")

ICON_IDLE = "🎙"
ICON_UP = "🎙•"
ICON_REC = "🎙 REC"


def _api_recording(timeout=1.0):
    """Return the /api/recording payload, or None if the server is not up."""
    try:
        with urllib.request.urlopen(URL + "/api/recording", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _fmt(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


class TranscriberBar(rumps.App):
    def __init__(self):
        super().__init__("Transcriber", title=ICON_IDLE, quit_button=None)
        self.proc = None            # server process we started (if any)
        self.open_when_up = False   # open the browser once the server answers
        self.starting_since = None

        self.item_status = rumps.MenuItem("Checking…")
        self.item_status.set_callback(None)
        self.item_toggle = rumps.MenuItem("Start Transcriber", callback=self.toggle)
        self.item_open = rumps.MenuItem("Open in Browser", callback=self.open_browser)
        self.item_log = rumps.MenuItem("Show Server Log", callback=self.show_log)
        self.item_folder = rumps.MenuItem("Show Recordings Folder", callback=self.show_folder)
        self.menu = [
            self.item_status,
            None,
            self.item_toggle,
            self.item_open,
            None,
            self.item_log,
            self.item_folder,
            None,
            rumps.MenuItem("Quit Transcriber", callback=self.quit),
        ]

        self.timer = rumps.Timer(self.refresh, 2)
        self.timer.start()
        self._diag_done = False
        log.info("launcher up: pid=%s python=%s bundle=%s", os.getpid(), sys.executable,
                 os.environ.get("__CFBundleIdentifier", "(not launched from a bundle)"))

        # One click = running. If nothing is on the port yet, start it.
        if _api_recording() is None:
            self.start_server()
        else:
            self.open_when_up = True
        self.refresh(None)

    # -- server lifecycle ----------------------------------------------------
    def start_server(self):
        if self.proc and self.proc.poll() is None:
            return
        if not os.path.exists(PYTHON):
            rumps.alert(
                "Transcriber is not set up",
                f"Expected a virtualenv at {PYTHON}.\n\n"
                "Follow the Setup Playbook in README.md (uv venv + pip install), "
                "then try again.",
            )
            return
        os.makedirs(LOG_DIR, exist_ok=True)
        env = dict(os.environ, TRANSCRIBER_RELOAD="0", PYTHONUNBUFFERED="1")
        log = open(LOG_PATH, "ab")
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} starting {APP_PY} =====\n".encode())
        self.proc = subprocess.Popen(
            [PYTHON, APP_PY], cwd=ROOT, env=env,
            stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group => clean stop
        )
        self.starting_since = time.time()
        self.open_when_up = True

    def stop_server(self):
        proc = self.proc
        self.proc = None
        self.open_when_up = False
        if not proc or proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        for _ in range(25):  # up to 5 s for a graceful exit
            if proc.poll() is not None:
                return
            time.sleep(0.2)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass

    # -- menu actions --------------------------------------------------------
    def toggle(self, _):
        if self.proc and self.proc.poll() is None:
            self.stop_server()
        elif _api_recording() is not None:
            rumps.alert(
                "Started elsewhere",
                f"Something is already serving {URL} (probably `python app.py` in a "
                "terminal). Stop it there; this menu only controls servers it started.",
            )
        else:
            self.start_server()
        self.refresh(None)

    def open_browser(self, _):
        webbrowser.open(URL)

    def show_log(self, _):
        os.makedirs(LOG_DIR, exist_ok=True)
        open(LOG_PATH, "a").close()
        subprocess.run(["open", LOG_PATH])

    def show_folder(self, _):
        subprocess.run(["open", os.path.join(ROOT, "recordings")])

    def quit(self, _):
        self.timer.stop()
        self.stop_server()
        rumps.quit_application()

    def _set_title(self, title):
        if title != self.title:
            log.info("menu bar title -> %r", title)
            self.title = title

    # -- status refresh ------------------------------------------------------
    def _diagnose_status_item(self):
        """Log whether macOS actually placed our icon in the menu bar."""
        if not hasattr(self, "_nsapp"):
            return  # called before run(); try again on the next timer tick
        self._diag_ticks = getattr(self, "_diag_ticks", 0) + 1
        if self._diag_ticks >= 8:  # ~16 s of samples is plenty
            self._diag_done = True
        try:
            item = self._nsapp.nsstatusitem
            win = item.button().window() if item.button() else None
            f = win.frame() if win else None
            import AppKit
            log.info("tick %d: status item visible=%s title=%r frame=%s policy=%s",
                     self._diag_ticks, bool(item.isVisible()), item.title(),
                     (f.origin.x, f.origin.y, f.size.width, f.size.height) if f else None,
                     AppKit.NSApplication.sharedApplication().activationPolicy())
        except Exception as e:  # pragma: no cover
            log.warning("status item diagnostics failed: %s", e)

    def refresh(self, _):
        if not self._diag_done:
            self._diagnose_status_item()
        status = _api_recording()
        mine = bool(self.proc and self.proc.poll() is None)

        if status is None:
            if mine and self.starting_since and time.time() - self.starting_since < 90:
                self._set_title(ICON_IDLE)
                self.item_status.title = "Starting… (loading speech model)"
            elif mine:
                self._set_title(ICON_IDLE)
                self.item_status.title = "Started but not answering — see log"
            else:
                if self.proc and self.proc.poll() is not None:
                    self.proc = None  # it died; allow a restart
                self._set_title(ICON_IDLE)
                self.item_status.title = "Not running"
            self.item_toggle.title = "Stop Transcriber" if mine else "Start Transcriber"
            self.item_open.set_callback(None)
            return

        # Server is up.
        self.starting_since = None
        self.item_open.set_callback(self.open_browser)
        if self.open_when_up:
            self.open_when_up = False
            webbrowser.open(URL)

        if status.get("is_recording"):
            label = status.get("mode_label") or "Recording"
            elapsed = _fmt(status.get("elapsed_seconds"))
            name = status.get("name") or "Untitled meeting"
            paused = " (paused)" if status.get("is_paused") else ""
            self._set_title(f"{ICON_REC} {elapsed}")
            self.item_status.title = f"Recording{paused}: {name} · {label} · {elapsed}"
        else:
            self._set_title(ICON_UP)
            where = "" if mine else " (started elsewhere)"
            self.item_status.title = f"Running at {URL}{where}"

        self.item_toggle.title = "Stop Transcriber" if mine else "Start Transcriber"


def _install_signal_handlers(bar):
    """Stop the server we started if we are terminated from outside the menu.
    Python runs signal handlers when it next gets control, which the 2 s
    status timer guarantees."""
    def _handler(signum, _frame):
        log.info("received signal %s; stopping server and quitting", signum)
        try:
            bar.timer.stop()
            bar.stop_server()
        finally:
            rumps.quit_application()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _handler)


if __name__ == "__main__":
    _bar = TranscriberBar()
    _install_signal_handlers(_bar)
    _bar.run()
