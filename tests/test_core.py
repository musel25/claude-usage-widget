"""Offline parsing and cache contracts using synthetic data only."""
import json
from datetime import datetime
import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.request

import claude_usage_core as core
from claude_usage_cli import render_dashboard


class UsageTests(unittest.TestCase):
    def test_all_reported_windows_preserve_percentages_and_grouping(self):
        rows = core.parse_usage({
            'five_hour': {'utilization': 0},
            'seven_day': {'utilization': 62.5},
            'seven_day_sonnet': {'utilization': 25},
            'seven_day_opus': {'utilization': 91},
        })
        self.assertEqual([r['pct'] for r in rows], [0, 62.5, 25, 91])
        self.assertEqual([r['is_session'] for r in rows], [True, False, False, False])
        self.assertIn('Sonnet', rows[2]['name'])
        self.assertIn('Opus', rows[3]['name'])

    def test_absent_windows_are_not_invented(self):
        for payload in ({}, {'seven_day': None}, {'seven_day': []}, {'unknown': {}}):
            self.assertEqual(core.parse_usage(payload), [])
        rows = core.parse_usage({'five_hour': {'utilization': 12}})
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['is_session'])

    def test_unknown_reset_and_elapsed_reset(self):
        self.assertEqual(core.format_reset(None), '')
        self.assertEqual(core.format_reset('2000-01-01T00:00:00Z'), 'Resets soon')

    def test_cache_round_trip_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache/usage.json'
            with patch.object(core, 'CACHE_PATH', str(path)):
                self.assertIsNone(core.load_cached_usage())
                sample = {'seven_day': {'utilization': 62.5}}
                core.save_cached_usage(sample)
                self.assertEqual(core.load_cached_usage(), sample)
                self.assertEqual(json.loads(path.read_text()), sample)
                path.write_text('{broken')
                self.assertIsNone(core.load_cached_usage())

    def test_cli_reports_limits_without_assuming_subscription(self):
        output = render_dashboard(core.parse_usage({'seven_day': {'utilization': 62}}))
        self.assertIn('62% used', output)
        self.assertIn('Weekly · All models', output)
        self.assertNotIn('MAX 5', output)

    def test_available_passes_keep_only_live_grants(self):
        payload = {'cedar_ember': {'grants': [
            {'label': 'Full reset', 'resets_left': 1, 'ends_at': '2026-12-01T00:00:00Z'},
            {'label': 'Used', 'resets_left': 0, 'ends_at': '2026-12-01T00:00:00Z'},
            {'label': 'Expired', 'resets_left': 1, 'ends_at': '2026-11-01T00:00:00Z'},
            {'label': 'Bad', 'resets_left': 1, 'ends_at': 'not-a-date'},
        ]}}
        passes = core.parse_reset_passes(payload, now='2026-11-19T00:00:00Z')
        self.assertEqual(passes, [{'label': 'Full reset', 'expires_at': '2026-12-01T00:00:00Z'}])
        self.assertEqual(core.parse_reset_passes({}, now='2026-11-19T00:00:00Z'), [])

    def test_cli_shows_pass_expiry(self):
        output = render_dashboard([], [{'label': 'Full reset', 'expires_at': '2026-12-01T00:00:00Z'}])
        self.assertIn('AVAILABLE LIMIT RESETS', output)
        self.assertIn('Full reset', output)
        local_day = datetime.fromisoformat('2026-12-01T00:00:00+00:00').astimezone()
        self.assertIn(f'{local_day.strftime("%b")} {local_day.day}', output)

    def test_pass_request_is_read_only_and_uses_claude_sign_in(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, '.credentials.json').write_text(json.dumps({'claudeAiOauth': {'accessToken': 'fake-token'}}))
            seen = []
            def open_request(opener, request, timeout):
                seen.append((request, timeout))
                return contextlib.closing(io.BytesIO(json.dumps({'cedar_ember': {'grants': []}}).encode()))
            with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': directory}), \
                    patch.object(subprocess, 'run', return_value=Mock(stdout='2.1.310 (Claude Code)')), \
                    patch.object(urllib.request.OpenerDirector, 'open', open_request):
                self.assertEqual(core.fetch_reset_passes(), [])
            request, timeout = seen[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.full_url, 'https://api.anthropic.com/api/oauth/usage?cedar_ember=1')
            self.assertEqual(request.get_header('Authorization'), 'Bearer fake-token')
            self.assertEqual(timeout, 15)


if __name__ == '__main__':
    unittest.main()
