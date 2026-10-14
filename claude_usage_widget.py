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
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, GLib, Gdk

CSS = b'''
window { background: transparent; font-family: Lato, sans-serif; }
.panel { background: #172337; border: 1px solid #3a4b64; border-radius: 22px; }
label { color: #f4f7fc; }
.title { font-size: 22px; font-weight: 800; }
.subtitle { color: #adbacd; font-size: 13px; }
.badge { color: #c4d9fa; background: #2a3d58; border-radius: 8px; padding: 5px 10px; font-size: 11px; font-weight: 700; }
.value { font-size: 66px; font-weight: 300; letter-spacing: -2px; }
.unit { color: #adbacd; font-size: 15px; padding-bottom: 15px; }
.remaining { color: #94bfff; font-size: 15px; font-weight: 700; }
.detail { font-size: 15px; font-weight: 700; }
.caption { color: #adbacd; font-size: 12px; }
.status { color: #adbacd; font-size: 11px; }
.warning { color: #f5c879; }
.error { color: #ff9292; }
.reset-box { background: #203047; border-radius: 12px; padding: 16px; }
button { background: transparent; background-image: none; color: #c4d9fa; border: 1px solid #475975; border-radius: 8px; padding: 7px 12px; box-shadow: none; text-shadow: none; font-size: 12px; }
button:hover { background: #304561; border-color: #94bfff; }
button:focus { border-color: #94bfff; outline: 2px solid #94bfff; outline-offset: 2px; }
button:disabled { color: #7a8aa1; border-color: #304159; }
.close { border: none; padding: 3px 6px; font-size: 18px; }
separator { background: #35445a; min-height: 1px; }
'''


def label(text: str, style: str, xalign: float = 0) -> Gtk.Label:
    item = Gtk.Label(label=text, xalign=xalign)
    item.get_style_context().add_class(style)
    return item


class Meter(Gtk.DrawingArea):
    """A quiet segmented meter, with a partially filled final segment."""

    def __init__(self):
        super().__init__()
        self.percent = None
        self.set_size_request(-1, 26)
        self.connect('draw', self.draw_meter)
        self.get_accessible().set_name('Usage meter')

    def update(self, percent):
        self.percent = percent
        self.get_accessible().set_description(
            'Usage unavailable' if percent is None else f'{percent:g}% of usage used'
        )
        self.queue_draw()

    def draw_meter(self, _widget, cr):
        count, gap, height = 32, 4, 12
        width = (self.get_allocated_width() - gap * (count - 1)) / count
        y = (self.get_allocated_height() - height) / 2
        color = (0.58, 0.75, 1.0)
        if self.percent is not None and self.percent >= 90:
            color = (1.0, 0.57, 0.57)
        elif self.percent is not None and self.percent >= 75:
            color = (0.96, 0.78, 0.47)
        filled = min(100, max(0, self.percent or 0)) / 100 * count
        for i in range(count):
            x = i * (width + gap)
            cr.set_source_rgb(0.21, 0.29, 0.40)
            cr.rectangle(x, y, width, height)
            cr.fill()
            fraction = min(1, max(0, filled - i))
            if fraction:
                cr.set_source_rgb(*color)
                cr.rectangle(x, y, width * fraction, height)
                cr.fill()
        return False


def make_row(name, sublabel, pct):
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    outer.set_margin_top(16)
    top = Gtk.Box(spacing=12)
    top.pack_start(label(name, 'detail'), False, False, 0)
    value = label(f'{pct:g}% used', 'caption', 1)
    if pct >= 90:
        value.get_style_context().add_class('error')
    elif pct >= 75:
        value.get_style_context().add_class('warning')
    top.pack_end(value, False, False, 0)
    outer.add(top)
    meter = Meter()
    meter.update(pct)
    outer.add(meter)
    if sublabel:
        outer.add(label(sublabel, 'caption'))
    return outer


class ClaudeUsageWindow(Gtk.Window):

    def __init__(self):
        super().__init__(title='Claude Usage')
        self.set_default_size(424, 50)
        self.busy = False
        self.alive = True
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
            self.connect('focus-out-event', lambda *_: (Gtk.main_quit(), False)[1])
        else:  # window
            self.set_keep_above(True)

        self.connect('key-press-event', self._on_key_press)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.get_style_context().add_class('panel')
        self.add(panel)
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(wrapper, f'set_margin_{side}')(28)
        panel.add(wrapper)

        header = Gtk.Box(spacing=10)
        header.pack_start(label('Claude', 'title'), False, False, 0)
        badge = label('Usage', 'badge')
        badge.set_valign(Gtk.Align.CENTER)
        header.pack_start(badge, False, False, 0)
        close = Gtk.Button(label='×')
        close.get_style_context().add_class('close')
        close.set_tooltip_text('Close (Esc)')
        close.get_accessible().set_name('Close widget')
        close.connect('clicked', lambda *_: self.destroy())
        header.pack_end(close, False, False, 0)
        wrapper.add(header)
        subtitle = label('Your week with Claude', 'subtitle')
        subtitle.set_margin_top(6)
        subtitle.set_margin_bottom(22)
        wrapper.add(subtitle)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        wrapper.add(self.content)
        divider = Gtk.Separator()
        divider.set_margin_top(20)
        divider.set_margin_bottom(16)
        wrapper.add(divider)
        footer = Gtk.Box(spacing=12)
        self.refresh_label = label('Connecting…', 'status')
        footer.pack_start(self.refresh_label, True, True, 0)
        self.refresh_button = Gtk.Button(label='Refresh')
        self.refresh_button.set_tooltip_text('Refresh usage (R)')
        self.refresh_button.connect('clicked', lambda *_: self._start_fetch())
        footer.pack_end(self.refresh_button, False, False, 0)
        wrapper.add(footer)
        hint = label('R to refresh  ·  Esc to close', 'status')
        hint.set_margin_top(16)
        wrapper.add(hint)

        self._has_usage = False
        cached = load_cached_usage()
        if cached:
            try:
                self._rebuild_content(parse_usage(cached))
                self.refresh_label.set_text('cached…')
                self._has_usage = True
            except Exception:
                self._show_loading()
        else:
            self._show_loading()

        self.connect('destroy', self._on_destroy)
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
        loading.get_style_context().add_class('caption')
        self.content.add(loading)
        self.content.show_all()

    def _on_key_press(self, _widget, event):
        if event.keyval in (Gdk.KEY_r, Gdk.KEY_R):
            self._start_fetch()
            return True
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

    def _on_destroy(self, *_):
        self.alive = False
        Gtk.main_quit()

    def _start_fetch(self):
        if self.busy or not self.alive:
            return
        self.busy = True
        self.refresh_button.set_sensitive(False)
        self.refresh_label.set_text('Updating…')
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self):
        try:
            data = fetch_usage()
            if not parse_usage(data):
                raise ValueError('No usage data returned. Check your session cookies in config.env.')
            save_cached_usage(data)
            GLib.idle_add(self._on_fetch_done, data, None)
        except subprocess.TimeoutExpired:
            GLib.idle_add(self._on_fetch_done, None, 'Request timed out.')
        except Exception as e:
            GLib.idle_add(self._on_fetch_done, None, f'Error: {e}')

    def _on_fetch_done(self, data, err):
        if not self.alive:
            return False
        self.busy = False
        self.refresh_button.set_sensitive(True)
        self.refresh_label.set_tooltip_text(err)
        if err:
            if self._has_usage:
                self.refresh_label.set_text('Saved usage · stale')
            else:
                self._show_error(err)
        else:
            self._has_usage = True
            self._rebuild_content(parse_usage(data))
        return False

    def _clear_content(self):
        for child in self.content.get_children():
            self.content.remove(child)

    def _show_error(self, msg):
        self._clear_content()
        lbl = Gtk.Label(label=msg, xalign=0, wrap=True)
        lbl.get_style_context().add_class('error')
        lbl.set_max_width_chars(42)
        self.content.add(lbl)
        self.refresh_label.set_text('Could not update')
        self.content.show_all()
        self.resize(424, 50)

    def _rebuild_content(self, rows):
        self._clear_content()

        # Promote the all-model weekly limit; keep every other reported limit.
        weekly = next((r for r in rows if r['name'] == 'Weekly · All models'), None)
        if weekly:
            self.content.add(label('Weekly usage', 'detail'))
            hero = Gtk.Box(spacing=12)
            hero.add(label(f"{weekly['pct']:g}%", 'value'))
            unit = label('used', 'unit')
            unit.set_valign(Gtk.Align.END)
            hero.add(unit)
            self.content.add(hero)
            meter = Meter()
            meter.update(weekly['pct'])
            self.content.add(meter)
            remaining = label(f"{max(0, 100 - weekly['pct']):g}% remaining this week", 'remaining')
            remaining.set_margin_top(8)
            remaining.set_margin_bottom(24)
            self.content.add(remaining)
            reset = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            reset.get_style_context().add_class('reset-box')
            reset.add(label('Weekly reset', 'caption'))
            reset.add(label(weekly['sublabel'] or 'Reset time not provided', 'detail'))
            self.content.add(reset)

        for row in rows:
            if row is not weekly:
                self.content.add(make_row(row['name'], row['sublabel'], row['pct']))

        if not rows:
            self._show_error('No usage data returned.')
            return

        self.refresh_label.set_text(f"Updated {datetime.now().strftime('%H:%M')}")
        self.content.show_all()
        self.resize(424, 50)


if __name__ == '__main__':
    win = ClaudeUsageWindow()
    Gtk.main()
