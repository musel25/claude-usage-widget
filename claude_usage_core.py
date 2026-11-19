"""Shared fetch/parse logic for the Claude usage widget and CLI."""

import json, os, re, shutil, subprocess, urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(
    os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'),
    'claude-usage-widget', 'usage.json',
)
PASS_CACHE_PATH = os.path.join(os.path.dirname(CACHE_PATH), 'passes.json')

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


def _timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt if dt.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def parse_reset_passes(payload, now=None):
    """Return only available banked grants, without grant IDs or account data."""
    current = _timestamp(now) if isinstance(now, str) else datetime.now(timezone.utc)
    if current is None or not isinstance(payload, dict):
        return []
    block = payload.get('cedar_ember')
    grants = block.get('grants') if isinstance(block, dict) else None
    if not isinstance(grants, list):
        return []
    passes = []
    for grant in grants:
        if not isinstance(grant, dict):
            continue
        expiry = _timestamp(grant.get('ends_at'))
        count = grant.get('resets_left')
        label = grant.get('label')
        if expiry and expiry > current and type(count) is int and count > 0 and isinstance(label, str) and label.strip():
            passes.extend({'label': label.strip(), 'expires_at': grant['ends_at']} for _ in range(min(count, 20)))
    return sorted(passes, key=lambda item: item['expires_at'])


def format_pass_expiry(expires_at, now=None):
    expiry = _timestamp(expires_at)
    if expiry is None:
        return 'Expiry unavailable'
    current = now or datetime.now(timezone.utc)
    local = expiry.astimezone()
    remaining = max(0, int((expiry - current).total_seconds()))
    days, remainder = divmod(remaining, 86400)
    hours = remainder // 3600
    left = f'{days}d {hours}h left' if days else f'{hours}h left' if hours else 'Less than 1h left'
    return f"Expires {local.strftime('%b')} {local.day} at {local.strftime('%I:%M %p %Z').lstrip('0')} · {left}"


def load_cached_passes():
    try:
        with open(PASS_CACHE_PATH) as stream:
            data = json.load(stream)
        if not isinstance(data, list):
            return None
        now = datetime.now(timezone.utc)
        return [item for item in data if isinstance(item, dict)
                and set(item) == {'label', 'expires_at'}
                and isinstance(item['label'], str) and item['label'].strip()
                and (expiry := _timestamp(item['expires_at'])) and expiry > now]
    except (OSError, ValueError):
        return None


def save_cached_passes(passes):
    try:
        os.makedirs(os.path.dirname(PASS_CACHE_PATH), exist_ok=True)
        with open(PASS_CACHE_PATH, 'w') as stream:
            json.dump(passes, stream)
    except OSError:
        pass


def fetch_reset_passes():
    """Read Claude Code's signed-in usage grant inventory. Never spend a grant."""
    path = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / '.credentials.json'
    try:
        token = json.loads(path.read_text())['claudeAiOauth']['accessToken']
        if not isinstance(token, str) or not token:
            raise ValueError
        binary = shutil.which('claude') or str(Path.home() / '.local/bin/claude')
        version = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=5).stdout.split()[0]
        if not re.fullmatch(r'\d+\.\d+\.\d+', version):
            raise ValueError
        request = urllib.request.Request(
            'https://api.anthropic.com/api/oauth/usage?cedar_ember=1',
            headers={'Authorization': 'Bearer ' + token,
                     'anthropic-beta': 'oauth-2025-04-20',
                     'User-Agent': f'claude-cli/{version} (external, cli)'})
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or not isinstance(payload.get('cedar_ember'), dict):
            raise ValueError
        return parse_reset_passes(payload)
    except Exception:
        raise RuntimeError('Limit reset passes unavailable') from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Limit reset passes unavailable')
