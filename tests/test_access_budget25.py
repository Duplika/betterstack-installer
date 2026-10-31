"""Synthetic skewed traffic, hard budgets and cursor continuity."""
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import collector as c
from test_scale import LINE, PROFILE


class AccessBudgetTests(unittest.TestCase):
    def test_hot_file_and_thousand_small_files_fit_without_skipping_or_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            domains = {'a.example.net': 'busy'}
            domains.update({'s%04d.example.net' % n: 'account' for n in range(1000)})
            for site in domains:
                (root / site).write_bytes(b'')
            profile = dict(PROFILE, domlog_roots=[folder])
            state = {}
            c.collect_access(c.Output(PROFILE, state), state, domains, profile)
            for site in domains:
                (root / site).write_bytes(LINE * (6000 if site == 'a.example.net' else 1))
            state['access_round_robin'] = 0
            state['access_at'] -= 60
            out = c.Output(PROFILE, state)
            coverage = c.collect_access(out, state, domains, profile)
            self.assertEqual(coverage['bytes_read'], len(LINE) * 7000)
            self.assertEqual(coverage['skipped_bytes'], 0)
            self.assertFalse(coverage.get('unparsed_lines', 0))
            self.assertEqual(coverage['files'], len(domains))
            for site in domains:
                self.assertEqual(state['access_files'][str(root / site)]['offset'],
                                 (root / site).stat().st_size)
            again = c.collect_access(c.Output(PROFILE, state), state, domains, profile)
            self.assertEqual(again['bytes_read'], 0)
            self.assertEqual(again['skipped_bytes'], 0)

    def test_per_file_cap_remains_hard_and_reports_partial_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'example.net'; path.write_bytes(b'')
            profile = dict(PROFILE, domlog_roots=[folder]); state = {}
            domains = {'example.net': 'account'}
            c.collect_access(c.Output(PROFILE, state), state, domains, profile)
            path.write_bytes(LINE * (c.MAX_FILE_BYTES // len(LINE) + 50))
            coverage = c.collect_access(c.Output(PROFILE, state), state, domains, profile)
            self.assertLessEqual(coverage['bytes_read'], c.MAX_FILE_BYTES)
            self.assertGreater(coverage['skipped_bytes'], 0)
            self.assertLessEqual(state['access_files'][str(path)]['offset'], path.stat().st_size)

    def test_discovery_growth_can_use_spare_capacity_without_reopening_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            domains = {'a.example.net': 'account', 'z.example.net': 'other'}
            for site in domains:
                (root / site).write_bytes(b'')
            profile = dict(PROFILE, domlog_roots=[folder]); state = {}
            c.collect_access(c.Output(PROFILE, state), state, domains, profile)
            (root / 'z.example.net').write_bytes(LINE)
            original = c.read_tail; calls = []
            def growing(path, previous, allowance):
                calls.append(path)
                if path.name == 'a.example.net':
                    path.write_bytes(LINE * 100)
                return original(path, previous, allowance)
            state['access_round_robin'] = 0
            with patch.object(c, 'read_tail', side_effect=growing), \
                    patch.object(c, 'MAX_CYCLE_BYTES', len(LINE) * 101):
                coverage = c.collect_access(c.Output(PROFILE, state), state, domains, profile)
            self.assertEqual(coverage['bytes_read'], len(LINE) * 101)
            self.assertEqual(coverage['skipped_bytes'], 0)
            self.assertEqual(len(calls), 2)

    def test_allocations_obey_caps_and_satisfy_every_tail_when_capacity_exists(self):
        randomizer = random.Random(25)
        for _ in range(100):
            demands = [randomizer.randrange(0, 1000) for _ in range(100)]
            budget = randomizer.randrange(0, 100000)
            grants = c.access_allowances(demands, budget)
            self.assertLessEqual(sum(grants), budget)
            self.assertTrue(all(0 <= g <= min(d, c.MAX_FILE_BYTES)
                                for g, d in zip(grants, demands)))
            if sum(demands) <= budget:
                self.assertEqual(grants, demands)
