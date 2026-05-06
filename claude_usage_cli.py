#!/usr/bin/python3
"""Claude usage dashboard, terminal edition. Prints once and exits."""

import subprocess, sys
from datetime import datetime

from claude_usage_core import fetch_usage, parse_usage


def rgb_fg(r, g, b): return f'\x1b[38;2;{r};{g};{b}m'
def rgb_bg(r, g, b): return f'\x1b[48;2;{r};{g};{b}m'
RESET = '\x1b[0m'
BOLD  = '\x1b[1m'
DIM   = '\x1b[2m'

WHITE     = rgb_fg(232, 232, 232)
GREY      = rgb_fg(136, 136, 136)
DARKGREY  = rgb_fg(102, 102, 102)
BLUE      = rgb_fg(74, 158, 255)
BLUE_BG   = rgb_bg(30, 58, 95)
ORANGE    = rgb_fg(245, 166, 35)
RED       = rgb_fg(255, 107, 107)
TROUGH_BG = rgb_bg(42, 42, 42)
BLUE_FILL_BG   = rgb_bg(74, 158, 255)
ORANGE_FILL_BG = rgb_bg(245, 166, 35)
RED_FILL_BG    = rgb_bg(255, 68, 68)

BAR_WIDTH = 36


def color_for(pct):
    if pct >= 80: return RED, RED_FILL_BG
    if pct >= 60: return ORANGE, ORANGE_FILL_BG
    return BLUE, BLUE_FILL_BG


def render_bar(pct):
    filled = int(round(min(pct, 100) / 100 * BAR_WIDTH))
    empty  = BAR_WIDTH - filled
    _, fill_bg = color_for(pct)
    return f'{fill_bg}{" " * filled}{TROUGH_BG}{" " * empty}{RESET}'


def render_row(row):
    pct = row['pct']
    fg, _ = color_for(pct)
    pct_text = f'{pct:.0f}% used'
    name = row['name']
    pad = max(1, BAR_WIDTH + 2 - len(name) - len(pct_text))
    out = [f'  {WHITE}{name}{RESET}{" " * pad}{fg}{pct_text}{RESET}']
    out.append(f'  {render_bar(pct)}')
    if row['sublabel']:
        out.append(f'  {DARKGREY}{row["sublabel"]}{RESET}')
    return '\n'.join(out)


def render_dashboard(rows):
    now = datetime.now().strftime('%H:%M')
    lines = []
    badge = f'{BLUE_BG}{BLUE} MAX 5× {RESET}'
    title = f'{BOLD}{WHITE}CLAUDE  USAGE{RESET}'
    updated = f'{DIM}{DARKGREY}updated {now}{RESET}'
    pad = max(1, BAR_WIDTH + 2 - len('CLAUDE  USAGE') - len(' MAX 5× ') - len(f'updated {now}') - 2)
    lines.append(f'{title} {badge}{" " * pad}{updated}')
    lines.append('')

    sessions = [r for r in rows if r['is_session']]
    weekly   = [r for r in rows if not r['is_session']]

    if sessions:
        lines.append(f'  {GREY}CURRENT SESSION{RESET}')
        lines.append('')
        for r in sessions:
            lines.append(render_row(r))
            lines.append('')

    if weekly:
        lines.append(f'  {GREY}WEEKLY LIMITS{RESET}')
        lines.append('')
        for r in weekly:
            lines.append(render_row(r))
            lines.append('')

    return '\n'.join(lines)


def main():
    try:
        data = fetch_usage()
        rows = parse_usage(data)
    except subprocess.TimeoutExpired:
        print(f'{RED}Request timed out.{RESET}', file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f'{RED}Error: {e}{RESET}', file=sys.stderr)
        sys.exit(1)

    if not rows:
        print(f'{RED}No usage data returned.{RESET}', file=sys.stderr)
        sys.exit(1)

    print()
    print(render_dashboard(rows))


if __name__ == '__main__':
    main()
