"""Sparse real observations and incident-only events, with synthetic inputs."""
import json
import unittest
from unittest.mock import patch
import collector as c

PROFILE = {'host': 'cloud.example.net', 'server_ip': '203.0.113.20'}


class SamplingTests(unittest.TestCase):
    def test_five_minute_snapshots_do_not_replay_metrics(self):
        state = {}; observations = []; metric_values = []
        for now in range(0, 601, 60):
            out = c.Output(PROFILE, state, now=now)
            def probe():
                observations.append(now); out.metric('real_snapshot', now); return True
            self.assertTrue(c.sampled_component(out, state, 'lve', probe, now))
            metric_values += [r['gauge']['value'] for r in out.metrics if r['name'] == 'real_snapshot']
            age = next(r['gauge']['value'] for r in out.metrics if r['name'] == 'duplika_lve_sample_age_seconds')
            self.assertEqual(age, now % 300)
            state = json.loads(json.dumps(state))
        self.assertEqual(observations, [0, 300, 600]); self.assertEqual(metric_values, observations)

    def test_failed_sample_remains_failed_until_successful_retry(self):
        state = {}; calls = []
        def failed():
            calls.append(0); raise RuntimeError('private output must never be logged')
        for now in (0, 60, 240):
            out = c.Output(PROFILE, state, now=now)
            self.assertEqual(c.sampled_component(out, state, 'lve', failed, now), 'failed')
            self.assertNotIn('private output', json.dumps(out.logs))
            self.assertFalse(any(r['name'] == 'duplika_lve_sample_age_seconds' for r in out.metrics))
        self.assertEqual(len(calls), 1)
        out = c.Output(PROFILE, state, now=300)
        self.assertTrue(c.sampled_component(out, state, 'lve', lambda: True, 300))
        self.assertEqual(next(r['gauge']['value'] for r in out.metrics if r['name'] == 'duplika_lve_sample_failed'), 0)

    def test_clock_reset_samples_again_and_components_independent(self):
        state = {}; calls = []
        for name, now in [('processes', 1000), ('lve', 1000), ('lve', 900)]:
            c.sampled_component(c.Output(PROFILE, state, now=now), state, name, lambda: calls.append(name), now)
        self.assertEqual(calls, ['processes', 'lve', 'lve'])


class IncidentTests(unittest.TestCase):
    def test_changing_process_counts_do_not_emit_routine_resource_logs(self):
        state = {}; logs = []
        for now in range(0, 1800, 60):
            out = c.Output(PROFILE, state, now=now)
            out.log('account_resources', user='account', blocked_processes=0, running_processes=now)
            out.metric('duplika_account_cpu_percent', 1, user='account')
            logs += out.logs
            self.assertEqual(len(out.metrics), 1)
        self.assertEqual(logs, [])

    def test_pressure_start_reminder_recovery_and_limits_change(self):
        state = {}; reasons = []
        for now, cpu, limit in [(0, 1, 100), (300, 85, 100), (600, 90, 100),
                                 (1200, 95, 100), (1500, 5, 100), (1800, 5, 200)]:
            out = c.Output(PROFILE, state, now=now)
            out.log('lve_usage', user='account', usage={'cpu_percent': cpu}, limits={'cpu_percent': limit})
            reasons += [r['log_reason'] for r in out.logs]
        self.assertEqual(reasons, ['limits_or_capability_changed', 'pressure_started', 'pressure_reminder',
                                  'pressure_recovered', 'limits_or_capability_changed'])

    def traffic(self, state, now, errors, complete=True):
        out = c.Output(PROFILE, state, now=now)
        out.log('site_traffic', site='example.net', user='account', requests=10, errors_5xx=errors,
                interval_seconds=60, coverage_complete=complete, breakdown={'route:home': 10})
        return out.logs

    def test_http_incident_counts_are_aggregated_without_false_recovery(self):
        state = {}
        self.assertEqual(self.traffic(state, 0, 0), [])
        self.assertEqual(self.traffic(state, 60, 2)[0]['log_reason'], 'http_5xx_started')
        self.assertEqual(self.traffic(state, 120, 3), [])
        self.assertEqual(self.traffic(state, 180, 0, complete=False), [])
        recovered = self.traffic(state, 240, 0)[0]
        self.assertEqual(recovered['log_reason'], 'http_5xx_recovered')
        self.assertEqual(recovered['requests'], 30); self.assertEqual(recovered['errors_5xx'], 3)
        self.assertFalse(recovered['coverage_complete'])
        self.assertEqual(state['traffic_incidents_v16'], {})

    def test_continuous_http_errors_remind_without_losing_counts(self):
        state = {}; logs = []
        for now in range(0, 961, 60): logs += self.traffic(state, now, 1)
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[1]['errors_5xx'], 15)

    def test_dynamic_heartbeat_counters_do_not_defeat_daily_policy(self):
        state = {}; logs = []
        for now in range(0, 1200, 60):
            out = c.Output(PROFILE, state, now=now)
            out.log('collector_heartbeat', accounts=100, domains=200,
                    components={'lve': True, 'processes': 100 + now,
                                'access': {'bytes_read': now * 100, 'files': 300}})
            logs += out.logs
        self.assertEqual(len(logs), 1)
        out = c.Output(PROFILE, state, now=1200)
        out.log('collector_heartbeat', accounts=100, domains=200, components={'lve': 'failed'})
        self.assertEqual(len(out.logs), 1)

    def test_partial_access_reminders_are_bounded_but_recovery_immediate(self):
        state = {}; logs = []
        for now in range(0, 961, 60):
            out = c.Output(PROFILE, state, now=now)
            out.log('access_log_coverage', coverage_complete=False, skipped_bytes=now)
            logs += out.logs
        self.assertEqual(len(logs), 2)
        out = c.Output(PROFILE, state, now=1020)
        out.log('access_log_coverage', coverage_complete=True)
        self.assertEqual(len(out.logs), 1)
