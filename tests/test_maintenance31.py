"""Daily maintenance: bounded read-only probes, failure semantics and delivery."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import health as h
import identity
import installer as i
from test_upgrade29 import Rc28EssentialPlanTests


def read(path):
    return Path(path).read_bytes()


def response(text='', code=0, err=''):
    return subprocess.CompletedProcess([], code, text, err)


class DailyTests(unittest.TestCase):
    def test_absent_litespeed_never_queries_network_or_php(self):
        with patch.object(h, 'litespeed_home', return_value=None), patch.object(h, 'catalog_text') as network:
            result = h.probe_litespeed(read, network)
        network.assert_not_called()
        self.assertEqual(result['status'], 'not_applicable')

    def test_probe_once_daily_including_errors_and_clock_rollback(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(h, 'probe_litespeed', return_value={'status': 'unknown'}) as probe:
            state = Path(directory)
            writer = lambda path, value: path.write_text(json.dumps(value))
            first = h.daily_litespeed(state, read, writer, 100000, online=True)
            self.assertEqual(h.daily_litespeed(state, read, writer, 100001, online=True), first)
            self.assertEqual(h.daily_litespeed(state, read, writer, 186399, online=True), first)
            self.assertEqual(probe.call_count, 1)
            h.daily_litespeed(state, read, writer, 186400, online=True)
            self.assertEqual(probe.call_count, 2)
            h.daily_litespeed(state, read, writer, 1, online=True)
            self.assertEqual(probe.call_count, 3)

    def test_verify_never_refreshes_or_writes_an_expired_cache(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(h, 'probe_litespeed') as probe:
            result = h.daily_litespeed(directory, read, lambda *a: self.fail('write'), 1000)
        self.assertIsNone(result['checked_at'])
        probe.assert_not_called()

    def test_metric_timestamp_is_original_and_unknown_is_not_healthy(self):
        record = {'checked_at': 1000, 'observation': {'server': {'status': 'unknown'},
                  'timezonedb': {'status': 'missing', 'php': [{'php': '83', 'status': 'missing'}]}}}
        rows = h.maintenance_metrics(record, {'host': 'cloud.example.net', 'server_ip': '203.0.113.20'})
        self.assertEqual(rows[0]['gauge']['value'], -1)
        self.assertEqual(rows[1]['gauge']['value'], 2)
        self.assertTrue(all(x['dt']=='1970-01-01T00:16:40+00:00' for x in rows))

    def test_untrusted_custom_home_is_not_sourced_or_silently_ignored(self):
        with patch.object(h.Path, 'exists', return_value=True):
            with self.assertRaises(ValueError):
                h.litespeed_home(lambda p: b'LSWS_HOME=$(echo unsafe)')

    def test_numeric_comparison_not_lexicographical(self):
        self.assertGreater(h.numeric_version('6.3.10'), h.numeric_version('6.3.9'))
        self.assertEqual(h.numeric_version('2026.6'), h.numeric_version('2026.6.0'))
        for bad in ('', '6.4rc1', '6.3.7;bad', '0.system'):
            with self.assertRaises(ValueError): h.numeric_version(bad)

    def test_catalog_bounds_and_errors(self):
        with patch.object(h, 'trusted_executable', return_value='/usr/bin/curl'), \
             patch.object(h, 'run', return_value=response('LSWS_STABLE=6.3.7 BUILD 7')) as run:
            h.catalog_text('https://update.litespeedtech.com/ws/latest.php')
        args = run.call_args[0][0]
        self.assertEqual(args[1], '-q')
        self.assertIn('--max-filesize', args)
        self.assertIn('--max-time', args)
        self.assertNotIn('--location', args)
        with self.assertRaises(ValueError): h.catalog_text('https://example.net/')
        with patch.object(h, 'trusted_executable', return_value='/usr/bin/curl'), \
             patch.object(h, 'run', return_value=response('<html>', 22)):
            with self.assertRaises(RuntimeError): h.catalog_text('https://update.litespeedtech.com/ws/latest.php')


class VersionTests(unittest.TestCase):
    def check(self, catalog, installed='6.3.7', build='7'):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home/'autoupdate').mkdir()
            (home/'VERSION').write_text(installed)
            (home/'BUILD').write_text('2')
            (home/('BUILD.'+installed)).write_text(build)
            (home/'autoupdate/release').write_text(installed+'-ent')
            with patch.object(h, 'litespeed_home', return_value=home), \
                 patch.object(h, 'timezone_observations', return_value={'status':'not_applicable','php':[]}):
                return h.probe_litespeed(read, lambda url: catalog)['server']

    def test_stable_version_build_ahead_and_current(self):
        for text, expected in [('LSWS_STABLE=6.3.7 BUILD 7','current'),
                               ('LSWS_STABLE=6.3.7 BUILD 8','build_available'),
                               ('LSWS_STABLE=6.3.8 BUILD 1','version_available'),
                               ('LSWS_STABLE=6.3.6 BUILD 999','ahead_of_stable')]:
            self.assertEqual(self.check(text)['status'], expected)
        self.assertEqual(self.check('LSWS_STABLE=6.3.7 BUILD 7')['installed_build'], 7)

    def test_html_ambiguous_and_candidate_catalogs_fail_closed(self):
        for text in ('<html>error</html>', 'LSWS_STABLE=6.4rc1 BUILD 1',
                     'LSWS_STABLE=6.3.7 BUILD 7\nLSWS_STABLE=6.3.8 BUILD 1'):
            self.assertEqual(self.check(text)['status'], 'unknown')


class TimezoneTests(unittest.TestCase):
    def run_probe(self, installed='2026.5', latest='2026.6', missing=False,
                  enabled=True, failed=False):
        with tempfile.TemporaryDirectory(dir='/root') as directory:
            php_root = Path(directory)
            root = php_root/'ea-php83/root'
            (root/'usr/bin').mkdir(parents=True)
            (root/'usr/bin/php').touch()
            (root/'usr/lib64/php/modules').mkdir(parents=True)
            if not missing: (root/'usr/lib64/php/modules/timezonedb.so').touch()
            (root/'etc/php.d').mkdir(parents=True)
            (root/'etc/php.d/02-pecl.ini').write_text('extension="timezonedb.so"' if enabled else ';extension=timezonedb.so')
            calls = []
            def runner(args, **kwargs):
                calls.append(args)
                self.assertEqual(kwargs['timeout'], 3)
                self.assertIn('-n', args)
                self.assertNotIn('-i', args)
                return response(installed, 1 if failed else 0)
            result = h.timezone_observations(read, lambda url: latest,
                       lambda p: p, runner, php_root)
            return result, calls

    def test_outdated_current_missing_and_disabled(self):
        self.assertEqual(self.run_probe()[0]['status'], 'update_available')
        self.assertEqual(self.run_probe(installed='2026.6')[0]['status'], 'current')
        self.assertEqual(self.run_probe(missing=True)[0]['status'], 'missing')
        result, calls = self.run_probe(enabled=False)
        self.assertEqual(result['status'], 'missing')
        self.assertEqual(calls, [])

    def test_empty_catalog_and_php_failure_not_current(self):
        self.assertEqual(self.run_probe(latest='')[0]['status'], 'unknown')
        self.assertEqual(self.run_probe(failed=True)[0]['status'], 'unknown')
        self.assertEqual(self.run_probe(installed='0.system')[0]['status'], 'unknown')


class Rc30UpgradeTests(Rc28EssentialPlanTests):
    previous_version = '4.0.0-rc30'


from test_distribution import IdentityDeliveryTests as DeliveryFixture


class MaintenanceDeliveryTests(DeliveryFixture):
    def record(self):
        return {'checked_at': 1000000, 'observation': {
            'status': 'installed', 'server': {'status': 'current'},
            'timezonedb': {'status': 'not_applicable', 'php': []}}}

    def test_metrics_sent_once_and_never_retimestamped_hourly(self):
        record = self.record()
        with patch.object(h, 'daily_litespeed', return_value=record):
            identity.status(publish=True)
            self.assertEqual(self.send.call_count, 2)
            metrics_call = self.send.call_args_list[0]
            self.assertTrue(metrics_call.args[0].endswith('/metrics'))
            self.assertIsInstance(metrics_call.args[2], list)
            original = metrics_call.args[2][0]['dt']
            self.clock.return_value += 3600
            identity.status(publish=True)
            self.assertEqual(self.send.call_count, 2)
            self.assertEqual(metrics_call.args[2][0]['dt'], original)

    def test_failed_metrics_delivery_retries_without_marking_it_delivered(self):
        record = self.record()
        with patch.object(h, 'daily_litespeed', return_value=record):
            self.send.side_effect = RuntimeError('delivery_unconfirmed')
            with self.assertRaises(RuntimeError): identity.status(publish=True)
            self.assertFalse((identity.STATE/'maintenance-metrics-send.json').exists())
            self.send.side_effect = None
            identity.status(publish=True)
            self.assertEqual(json.loads((identity.STATE/'maintenance-metrics-send.json').read_text())['checked_at'], 1000000)
