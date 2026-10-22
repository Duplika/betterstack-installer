"""Reproduce systemd's old oneshot job still active after process lock release."""
import subprocess
import time
import unittest
from unittest.mock import patch
import installer as i


class JobRaceTests(unittest.TestCase):
    def test_old_job_finishes_before_a_new_start_is_allowed(self):
        states = iter(['activating', 'active', 'inactive']); events = []
        def run(args):
            state = next(states); events.append(state)
            return subprocess.CompletedProcess(args, 0, state + '\n', '')
        with patch.object(i, 'run', side_effect=run), patch.object(i.time, 'sleep'):
            i.wait_observer_inactive()
            events.append('new start')
        self.assertEqual(events, ['activating', 'active', 'inactive', 'new start'])

    def test_failed_unknown_and_stuck_job_do_not_trigger_a_new_start(self):
        for state, reason in [('failed', 'previous_observer_job_failed'),
                              ('', 'state_unavailable'), ('active', 'busy_retry_later')]:
            with patch.object(i, 'run', return_value=subprocess.CompletedProcess([], 0, state, '')):
                with self.assertRaisesRegex(RuntimeError, reason): i.wait_observer_inactive(timeout=0)

    def test_missing_old_stale_or_failed_markers_never_accept_upgrade(self):
        for marker in [
            {'at': time.time(), 'components': {'lve': True}},
            {'at': time.time(), 'agent_version': i.VERSION, 'started_at': 50, 'components': {'lve': True}},
            {'at': 0, 'agent_version': i.VERSION, 'started_at': 110, 'components': {'lve': True}},
            {'at': time.time(), 'agent_version': i.VERSION, 'started_at': 110,
             'components': {'lve': 'failed', 'processes': 100, 'access': {}}},
        ]:
            with patch.object(i, 'load', return_value=marker):
                with self.assertRaisesRegex(RuntimeError, 'new_observer_cycle_not_verified'):
                    i.verify_new_observer_cycle(100)
        with patch.object(i, 'load', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, 'new_observer_cycle_marker_missing'):
                i.verify_new_observer_cycle(100)
        with patch.object(i, 'load', return_value={'at': time.time(), 'agent_version': i.VERSION,
                         'started_at': 110, 'components': {'lve': True, 'processes': 100, 'access': {}}}):
            i.verify_new_observer_cycle(100)
