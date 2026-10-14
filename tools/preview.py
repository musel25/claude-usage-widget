"""Render a synthetic preview without reading credentials or fetching usage."""
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('GDK_BACKEND', 'x11')

from datetime import datetime, timedelta, timezone
import claude_usage_core as core

with patch.object(core, 'load_config', return_value={'WIDGET_MODE': 'window'}):
    import claude_usage_widget as w

with patch.object(w, 'load_cached_usage', return_value=None), \
        patch.object(w.ClaudeUsageWindow, '_start_fetch'):
    win = w.ClaudeUsageWindow()
reset = (datetime.now(timezone.utc) + timedelta(days=2, hours=2)).isoformat()
win._on_fetch_done({
    'seven_day': {'utilization': 62, 'resets_at': reset},
    'five_hour': {'utilization': 36},
    'seven_day_sonnet': {'utilization': 25},
    'seven_day_opus': {'utilization': 91},
}, None)

output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/widget-preview.png')
errors = []


def capture():
    try:
        pix = w.Gdk.pixbuf_get_from_window(win.get_window(), 0, 0, *win.get_size())
        if pix is None:
            raise RuntimeError('The widget window could not be captured')
        pix.savev(str(output), 'png', [], [])
        print(f'Preview saved to {output}')
    except Exception as exc:
        errors.append(exc)
    finally:
        # Both widgets normally shut down their application on destroy.
        with patch.object(w.Gtk, 'main_quit'):
            win.destroy()
        w.Gtk.main_quit()
    return False


w.GLib.timeout_add(500, capture)
w.Gtk.main()
if errors:
    raise SystemExit(str(errors[0]))
