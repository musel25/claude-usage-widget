#!/usr/bin/python3
"""
Claude Usage Widget — GTK3 GUI.

WIDGET_MODE in config.env controls behavior:
  "popup"   — on-demand window, closes on Esc or focus-out (default)
  "desktop" — undecorated, pinned to bottom-left corner, behaves like a desktop widget
  "window"  — original floating window, decorated, kept above

Credentials and mode live in config.env (same directory as this script).
"""

import os, sys, subprocess, threading
from datetime import datetime

from claude_usage_core import (
    load_config, fetch_usage, parse_usage,
    load_cached_usage, save_cached_usage,
)

REFRESH_SECONDS = 300  # 5 minutes

_cfg = load_config()
WIDGET_MODE = _cfg.get('WIDGET_MODE', 'popup').lower()
try:
    AUTO_CLOSE_SECONDS = int(_cfg.get('AUTO_CLOSE_SECONDS', '0') or 0)
except ValueError:
    AUTO_CLOSE_SECONDS = 0

# DOCK/utility-style hints aren't honored on GNOME/Wayland, so re-exec under
# XWayland before GTK initializes when we need them.
if WIDGET_MODE in ('popup', 'desktop') and os.environ.get('GDK_BACKEND') != 'x11':
    os.environ['GDK_BACKEND'] = 'x11'
    os.execv(sys.executable, [sys.executable] + sys.argv)

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk

CSS = b"""
window {
    background-color: #111111;
}
.header-label {
    color: #ffffff;
    font-size: 13px;
    font-weight: 500;
    letter-spacing: 1px;
}
.badge {
    background-color: #1e3a5f;
    border-radius: 6px;
    padding: 2px 8px;
}
.badge-label {
    color: #4a9eff;
    font-size: 10px;
    font-weight: 500;
}
.row-name {
    color: #e8e8e8;
    font-size: 12px;
}
.row-sub {
    color: #666666;
    font-size: 10px;
}
.pct-label {
    color: #aaaaaa;
    font-size: 11px;
}
.pct-high {
    color: #ff6b6b;
    font-size: 11px;
}
.section-title {
    color: #888888;
    font-size: 10px;
    letter-spacing: 1.5px;
}
.refresh-label {
    color: #444444;
    font-size: 9px;
}
.error-label {
    color: #ff6b6b;
    font-size: 11px;
}
progress, trough {
    min-height: 4px;
    border-radius: 2px;
}
progress {
    background-color: #4a9eff;
    border-radius: 2px;
}
trough {
    background-color: #2a2a2a;
    border-radius: 2px;
}
.progress-warn progress {
    background-color: #f5a623;
}
.progress-danger progress {
    background-color: #ff4444;
}
"""


def make_row(name, sublabel, pct):
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    outer.set_margin_bottom(14)

    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

    name_label = Gtk.Label(label=name, xalign=0)
    name_label.get_style_context().add_class('row-name')

    pct_label = Gtk.Label(label=f'{pct:.0f}% used', xalign=1)
    pct_label.get_style_context().add_class('pct-high' if pct >= 80 else 'pct-label')
    pct_label.set_hexpand(True)

    top.pack_start(name_label, False, False, 0)
    top.pack_end(pct_label, False, False, 0)

    bar = Gtk.ProgressBar()
    bar.set_fraction(min(pct / 100.0, 1.0))
    bar.set_hexpand(True)
    if pct >= 80:
        bar.get_style_context().add_class('progress-danger')
    elif pct >= 60:
        bar.get_style_context().add_class('progress-warn')

    outer.add(top)
    outer.add(bar)

    if sublabel:
        sub = Gtk.Label(label=sublabel, xalign=0)
        sub.get_style_context().add_class('row-sub')
        outer.add(sub)

    return outer


class ClaudeUsageWindow(Gtk.Window):

    def __init__(self):
        super().__init__(title='Claude Usage')
        self.set_default_size(360, 50)
        self.set_resizable(False)
        self.set_border_width(0)

        if WIDGET_MODE == 'desktop':
            self.set_decorated(False)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        elif WIDGET_MODE == 'popup':
            self.set_decorated(False)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_keep_above(True)
            self.set_position(Gtk.WindowPosition.CENTER)
            self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            self.connect('key-press-event', self._on_key_press)
            self.connect('focus-out-event', lambda *_: (Gtk.main_quit(), False)[1])
        else:  # window
            self.set_keep_above(True)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        wrapper.set_margin_top(16)
        wrapper.set_margin_bottom(16)
        wrapper.set_margin_start(20)
        wrapper.set_margin_end(20)
        self.add(wrapper)

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_bottom(16)

        title = Gtk.Label(label='CLAUDE  USAGE', xalign=0)
        title.get_style_context().add_class('header-label')

        badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        badge_box.get_style_context().add_class('badge')
        badge_box.set_margin_start(6)
        badge_lbl = Gtk.Label(label='MAX 5×')
        badge_lbl.get_style_context().add_class('badge-label')
        badge_box.add(badge_lbl)

        self.refresh_label = Gtk.Label(label='', xalign=1)
        self.refresh_label.get_style_context().add_class('refresh-label')
        self.refresh_label.set_hexpand(True)

        header.pack_start(title, False, False, 0)
        header.pack_start(badge_box, False, False, 0)
        header.pack_end(self.refresh_label, True, True, 0)
        wrapper.add(header)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        wrapper.add(self.content)

        self._showing_cached = False
        cached = load_cached_usage()
        if cached:
            try:
                self._rebuild_content(parse_usage(cached))
                self.refresh_label.set_text('cached…')
                self._showing_cached = True
            except Exception:
                self._show_loading()
        else:
            self._show_loading()

        self.connect('destroy', Gtk.main_quit)
        if WIDGET_MODE == 'desktop':
            self.connect('size-allocate', lambda *_: self._anchor_to_corner())
        self.show_all()

        self._start_fetch()
        if WIDGET_MODE != 'popup':
            GLib.timeout_add_seconds(REFRESH_SECONDS, self._refresh_tick)

        if AUTO_CLOSE_SECONDS > 0:
            GLib.timeout_add_seconds(AUTO_CLOSE_SECONDS, lambda: (Gtk.main_quit(), False)[1])

    def _show_loading(self):
        self._clear_content()
        loading = Gtk.Label(label='Fetching usage data…', xalign=0)
        loading.get_style_context().add_class('row-sub')
        self.content.add(loading)
        self.content.show_all()

    def _on_key_press(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
            return True
        return False

    def _anchor_to_corner(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geom = monitor.get_geometry()
        x_margin, y_margin = 90, 16
        _, win_h = self.get_size()
        self.move(geom.x + x_margin, geom.y + geom.height - win_h - y_margin)

    def _refresh_tick(self):
        self._start_fetch()
        return True

    def _start_fetch(self):
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self):
        try:
            data = fetch_usage()
            save_cached_usage(data)
            GLib.idle_add(self._on_fetch_done, data, None)
        except subprocess.TimeoutExpired:
            GLib.idle_add(self._on_fetch_done, None, 'Request timed out.')
        except Exception as e:
            GLib.idle_add(self._on_fetch_done, None, f'Error: {e}')

    def _on_fetch_done(self, data, err):
        if err:
            if self._showing_cached:
                self.refresh_label.set_text('(stale)')
            else:
                self._show_error(err)
        else:
            self._showing_cached = False
            self._rebuild_content(parse_usage(data))
        return False

    def _clear_content(self):
        for child in self.content.get_children():
            self.content.remove(child)

    def _show_error(self, msg):
        self._clear_content()
        lbl = Gtk.Label(label=msg, xalign=0, wrap=True)
        lbl.get_style_context().add_class('error-label')
        self.content.add(lbl)
        self.content.show_all()
        self.resize(360, 50)

    def _rebuild_content(self, rows):
        self._clear_content()

        session_rows = [r for r in rows if r['is_session']]
        weekly_rows  = [r for r in rows if not r['is_session']]

        if session_rows:
            sec = Gtk.Label(label='CURRENT SESSION', xalign=0)
            sec.get_style_context().add_class('section-title')
            sec.set_margin_bottom(10)
            self.content.add(sec)
            for r in session_rows:
                self.content.add(make_row(r['name'], r['sublabel'], r['pct']))

        if weekly_rows:
            div = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            div.set_margin_top(4)
            div.set_margin_bottom(12)
            self.content.add(div)
            sec2 = Gtk.Label(label='WEEKLY LIMITS', xalign=0)
            sec2.get_style_context().add_class('section-title')
            sec2.set_margin_bottom(10)
            self.content.add(sec2)
            for r in weekly_rows:
                self.content.add(make_row(r['name'], r['sublabel'], r['pct']))

        if not rows:
            self._show_error('No usage data returned.')
            return

        self.refresh_label.set_text(f"updated {datetime.now().strftime('%H:%M')}")
        self.content.show_all()
        self.resize(360, 50)


if __name__ == '__main__':
    win = ClaudeUsageWindow()
    Gtk.main()
