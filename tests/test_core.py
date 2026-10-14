"""Offline parsing and cache contracts using synthetic data only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == '__main__':
    unittest.main()
