"""Capacity and coverage checks with synthetic accounts, never customer logs."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import capabilities
import collector

PROFILE = {'host': 'cloud.example.net', 'server_ip': '203.0.113.20'}
LINE = b'127.0.0.1 - - [21/Oct/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 1\n'


class ScaleTests(unittest.TestCase):
    def test_cloudlinux8_shared_requires_real_capability_probe(self):
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'os-release'
            path.write_text('ID=cloudlinux\nVERSION_ID="8.10"\n')
            def run(args):
                value = ('CloudLinux OS Shared Pro' if '--detect-edition' in args else
                         '--domains' if '--help' in args else
                         '{"result":"success","users":[],"mySqlGov":"disabled"}')
                return subprocess.CompletedProcess(args, 0, value, '')
            caps = capabilities.detect(path, run, lambda name: '/usr/bin/' + name)
            self.assertEqual(caps['os_version'], '8.10')
            self.assertEqual(caps['lve_status'], 'available')
            self.assertTrue(caps['isolates_supported'])
            with self.assertRaisesRegex(capabilities.ProbeError, 'lve:invalid_json'):
                capabilities.detect(path, lambda args: subprocess.CompletedProcess(
                    args, 0, 'CloudLinux OS Shared Pro' if '--detect-edition' in args else 'invalid', ''),
                    lambda name: '/usr/bin/' + name)

    def test_thousand_busy_domains_all_sampled_with_same_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            domains = {'s%04d.example.net' % i: 'account%d' % (i // 2) for i in range(1000)}
            profile = dict(PROFILE, domlog_roots=[str(root)])
            for site in domains:
                (root / site).write_bytes(LINE * 200)
            state = {}
            first = collector.collect_access(collector.Output(PROFILE, state), state, domains, profile)
            self.assertEqual(first['initialized'], 1000)
            self.assertEqual(first['bytes_read'], 0)  # No historical upload.
            for cycle in range(2):
                for path in root.iterdir():
                    with path.open('ab') as stream:
                        stream.write(LINE * 200)
                state['access_at'] -= 60
                out = collector.Output(PROFILE, state)
                coverage = collector.collect_access(out, state, domains, profile)
                self.assertLessEqual(coverage['bytes_read'], collector.MAX_CYCLE_BYTES)
                self.assertGreater(coverage['skipped_bytes'], 0)
                sampled = {row['tags']['site'] for row in out.metrics
                           if row['name'] == 'duplika_site_requests_per_second' and row['gauge']['value'] > 0}
                self.assertEqual(sampled, set(domains))
                quality = next(row for row in out.metrics if row['name'] == 'duplika_access_coverage_complete')
                self.assertEqual(quality['gauge']['value'], 0)
                state = json.loads(json.dumps(state))

    def test_idle_files_do_not_take_budget_and_duplicates_are_not_counted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            domains = {'busy.example.net': 'busy', 'idle.example.net': 'idle'}
            busy, idle = root / 'busy.example.net', root / 'idle.example.net'
            busy.write_bytes(b''); idle.write_bytes(LINE)
            profile = dict(PROFILE, domlog_roots=[str(root), str(root)])
            state = {}
            collector.collect_access(collector.Output(PROFILE, state), state, domains, profile)
            busy.write_bytes(LINE * 20)
            state['access_at'] -= 60
            out = collector.Output(PROFILE, state)
            with patch.object(collector, 'MAX_CYCLE_BYTES', len(LINE) * 20):
                coverage = collector.collect_access(out, state, domains, profile)
            self.assertEqual(coverage['files'], 2)
            self.assertEqual(coverage['skipped_bytes'], 0)
            self.assertTrue(next(x for x in out.logs if x['event_kind'] == 'access_log_coverage')['coverage_complete'])
            requests = next(x for x in out.metrics if x['name'] == 'duplika_site_requests_per_second')
            self.assertAlmostEqual(requests['gauge']['value'] * 60, 20, delta=.02)

    def test_rotation_and_deleted_domains_keep_coverage_honest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / 'busy.example.net'; path.write_bytes(LINE)
            profile = dict(PROFILE, domlog_roots=[str(root)])
            domains = {'busy.example.net': 'busy'}; state = {}
            collector.collect_access(collector.Output(PROFILE, state), state, domains, profile)
            path.rename(root / 'old'); path.write_bytes(LINE * 5)
            state['access_at'] -= 60
            out = collector.Output(PROFILE, state)
            coverage = collector.collect_access(out, state, domains, profile)
            self.assertEqual(coverage['rotated'], 1)
            collector.collect_access(collector.Output(PROFILE, state), state, {}, profile)
            self.assertEqual(state['access_files'], {})
            self.assertEqual(state['traffic_pending_v4'], {})

    def test_isolated_domain_logs_thinned_but_pressure_and_metrics_kept(self):
        state = {}; logs = []; metrics = []
        for now in (60, 120, 180, 240, 300):
            out = collector.Output(PROFILE, state, now=now)
            for site in ('one.example.net', 'two.example.net'):
                out.log('isolated_domain_usage', user='account', site=site,
                        usage={'cpu_percent': 1}, limits={'cpu_percent': 100})
                out.metric('duplika_isolated_domain_cpu_percent', 1, user='account', site=site)
            logs.extend(out.logs); metrics.extend(out.metrics)
        self.assertEqual(len(logs), 2)
        self.assertEqual(len(metrics), 10)
        for now in (360, 420):
            out = collector.Output(PROFILE, state, now=now)
            out.log('isolated_domain_usage', user='account', site='one.example.net',
                    usage={'cpu_percent': 85}, limits={'cpu_percent': 100})
            self.assertEqual(len(out.logs), 1)
