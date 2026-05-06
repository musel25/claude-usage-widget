"""Shared fetch/parse logic for the Claude usage widget and CLI."""

import json, os, subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(
    os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'),
    'claude-usage-widget', 'usage.json',
)

# Actual API field → (display name, is_session)
FIELD_MAP = {
    'five_hour':        ('Current session (5h)', True),
    'seven_day':        ('Weekly · All models',  False),
    'seven_day_sonnet': ('Weekly · Sonnet',      False),
    'seven_day_opus':   ('Weekly · Opus',        False),
}


def load_config():
    cfg = {}
    path = os.path.join(SCRIPT_DIR, 'config.env')
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def fetch_usage():
    cfg = load_config()
    cmd = [
        'curl', '-s', '--max-time', '10',
        f"https://claude.ai/api/organizations/{cfg['ORG_ID']}/usage",
        '-H', 'accept: */*',
        '-H', 'anthropic-client-platform: web_claude_ai',
        '-H', 'anthropic-client-version: 1.0.0',
        '-H', 'content-type: application/json',
        '-H', 'referer: https://claude.ai/settings/usage',
        '-H', 'user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
        '-b', f"sessionKey={cfg['SESSION_KEY']}; cf_clearance={cfg['CF_CLEARANCE']}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return json.loads(result.stdout)


def load_cached_usage():
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_cached_usage(data):
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, 'w') as f:
            json.dump(data, f)
    except OSError:
        pass


def format_reset(resets_at):
    if not resets_at:
        return ''
    try:
        dt = datetime.fromisoformat(resets_at.replace('Z', '+00:00'))
        now = datetime.now(dt.tzinfo)
        diff = dt - now
        total = int(diff.total_seconds())
        if total <= 0:
            return 'Resets soon'
        h, m = divmod(total // 60, 60)
        return f'Resets in {h} hr {m} min' if h else f'Resets in {m} min'
    except Exception:
        return str(resets_at)


def parse_usage(data):
    rows = []
    for key, (display, is_session) in FIELD_MAP.items():
        item = data.get(key)
        if not isinstance(item, dict):
            continue
        rows.append({
            'name':       display,
            'sublabel':   format_reset(item.get('resets_at') or ''),
            'pct':        float(item.get('utilization') or 0.0),
            'is_session': is_session,
        })
    return rows
