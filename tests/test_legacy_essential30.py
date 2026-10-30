"""Reviewed legacy sampling keeps the WordPress error graph and cursors intact."""
import base64
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import tarfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import capabilities
import error_aggregation
import identity
import installer as i
import legacy_essential as e
import legacy_upgrade
import test_release18 as previous
from test_context_cleanup import fixture, TARGET
from test_upgrade29 import Rc28EssentialPlanTests


class Rc29ObserverPlanTests(Rc28EssentialPlanTests):
    previous_version = '4.0.0-rc29'


def rows():
    return '\n'.join(json.dumps(x) for x in [
        *[{'name': name, 'counter': {'value': 0}, 'tags': {'device': 'sda'}} for name in sorted(e.DISK)],
        {'name': 'filesystem_total_bytes', 'gauge': {'value': 100}, 'tags': {'mountpoint': '/'}},
        *[{'name': name, 'counter': {'value': 0}, 'tags': {'device': 'eth0'}}
          for name in ('network_receive_bytes_total', 'network_transmit_bytes_total')]])


class GraphTests(unittest.TestCase):
    def test_only_metric_sampling_changes_not_log_readers_or_destinations(self):
        original = error_aggregation.prepare(i.context_cleanup.prepare(fixture()))
        before = copy.deepcopy(original)
        changed = e.prepare(original)
        self.assertEqual(original, before)
        self.assertEqual(changed['data_dir'], original['data_dir'])
        for name, source in original['sources'].items():
            if name != 'duplika_host':
                self.assertEqual(changed['sources'][name], source)
        for name, transform in original['transforms'].items():
            if name != 'host_to_json':
                self.assertEqual(changed['transforms'][name], transform)
        for name, sink in original['sinks'].items():
            actual = copy.deepcopy(changed['sinks'][name])
            if name == 'duplika_metrics':
                actual['inputs'] = sink['inputs']
            self.assertEqual(actual, sink)
        self.assertEqual(changed['sources']['duplika_host']['filesystem'], original['sources']['duplika_host']['filesystem'])
        self.assertEqual(changed['sources']['duplika_host_slow']['collectors'], ['disk', 'network'])
        self.assertEqual(changed['sources']['duplika_host_slow']['scrape_interval_secs'], 300)
        self.assertNotIn('disk', changed['sources']['duplika_host']['collectors'])

    def test_native_coverage_requires_complete_real_families_and_accepts_zero(self):
        self.assertEqual(e.coverage(rows()), {'disk_io_devices_validated': 1,
            'disk_io_counter_samples_validated': 4, 'filesystem_mountpoints_validated': 1,
            'network_counters_validated': 2})
        for text in ('', '{}', 'not JSON', rows().replace('disk_read_bytes_total', 'unexpected'),
                     rows().replace('"value": 0', '"value": true'), rows().replace('filesystem_total_bytes', 'unexpected')):
            with self.assertRaises(RuntimeError):
                e.coverage(text)

    def test_probe_runs_as_transport_user_without_remote_sink_and_removes_files(self):
        paths = []
        def run(args, **kwargs):
            self.assertEqual(args[:4], ['runuser', '-u', 'vector', '--'])
            path = Path(args[-1]); paths.append(path)
            cfg = json.loads(path.read_text())
            self.assertEqual(cfg['sinks']['local']['type'], 'console')
            self.assertNotIn('TEST_SECRET', path.read_text())
            self.assertNotIn('http', path.read_text())
            self.assertEqual(cfg['sources']['probe']['scrape_interval_secs'], 60)
            return subprocess.CompletedProcess(args, 124, rows(), '')
        api = SimpleNamespace(execute=lambda args: SimpleNamespace(stdout='User=vector\nGroup=vector\n'),
                              trusted_executable=lambda name: '/usr/bin/timeout', run=run)
        old = {'services': ['vector.service'], 'vector_binary': '/usr/bin/vector'}
        with patch.object(e.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=0, pw_gid=0)):
            result = e.native_probe(api, old, fixture())
        self.assertEqual(result['disk_io_counter_samples_validated'], 4)
        self.assertFalse(paths[0].exists())
        self.assertFalse(paths[0].parent.exists())

    def test_probe_rejects_other_user_before_launching_vector(self):
        api = SimpleNamespace(execute=lambda args: SimpleNamespace(stdout='User=root\nGroup=root\n'), run=None)
        with patch.object(e.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=0, pw_gid=0)):
            with self.assertRaisesRegex(RuntimeError, 'transport_user_requires_review'):
                e.native_probe(api, {'services': ['vector.service']}, fixture())


class LegacyMigrationTests(previous.LegacyTests):
    release = '4.0.0-rc21'

    def setUp(self):
        super().setUp()
        script = (Path(__file__).parents[1]/'releases'/self.release/'installer.sh').read_text()
        encoded = script.split("<<'DUPLIKA_4_PAYLOAD'\n", 1)[1].split('DUPLIKA_4_PAYLOAD', 1)[0]
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)), mode='r:gz') as archive:
            files = {m.name: archive.extractfile(m).read() for m in archive}
        for path in i.runtime_files():
            path.write_bytes(files[path.name])
        proof = json.loads((i.HERE/('upgrade_'+self.release.split('-')[1]+'.json')).read_text())
        (i.UNITS/(i.NAME+'.service')).write_text(proof['identity_service_template'].replace('__PYTHON__', i.sys.executable))
        self.caps = {'os_id': 'almalinux', 'os_version': '8.10'}
        self.meta.update(agent_version=self.release, installer_version=self.release, capabilities=self.caps)
        self.path = Path(self.meta['config_path']); self.path.chmod(0o640)
        if self.variant.endswith('minimal-v4'):
            self.path.write_text(json.dumps(error_aggregation.prepare(json.loads(self.path.read_text()))))
            self.meta['migration_review'] = {'error_aggregation': error_aggregation.MARKER}
        self.save()
        self.stack.enter_context(patch.object(identity, 'status', return_value={}))
        self.stack.enter_context(patch.object(i.context_cleanup, 'validate'))
        self.stack.enter_context(patch.object(e, 'native_probe', return_value=e.coverage(rows())))
        self.stack.enter_context(patch.object(i, 'trusted_executable', side_effect=lambda name:
            i.sys.executable if name == i.sys.executable else str(self.vector) if name == 'vector' else None))
        transport = self.meta['services'][0]
        self.stack.enter_context(patch.object(i, 'run', side_effect=lambda args, **kw:
            subprocess.CompletedProcess(args, 0 if args[:2] != ['systemctl', 'is-active'] or args[-1] in self.meta['services'] else 3, 'loaded', '')))

    def save(self):
        self.meta['baseline'] = identity.snapshot(self.meta['critical_paths'], self.meta['config_path'])
        (i.BASE/'manifest.json').write_text(json.dumps(self.meta))
        (i.BASE/'complete').write_text(self.meta['agent_version']+'\n')

    # Parent fixture supplies the immutable runtime; its unrelated generic
    # migration assertions do not describe this explicitly selected policy.
    test_blueprint_exact_match_then_rebaselined_helper_or_config_change_rejected = None
    test_inherited_pipeline_and_offsets_are_not_touched = None
    test_bad_helper_or_added_critical_path_rejected = None

    def test_preflight_rejects_rebaselined_log_change_before_writes(self):
        e.plan(i, TARGET, self.caps)
        cfg = json.loads(self.path.read_text()); cfg['sources']['duplika_wordpress']['include'] = ['/unexpected']
        self.path.write_text(json.dumps(cfg)); self.save()
        with patch.object(i, 'backup') as backup:
            with self.assertRaisesRegex(RuntimeError, 'differs_from_blueprint'):
                e.migrate(i, TARGET, self.caps, apply=True)
        backup.assert_not_called()

    def test_second_transport_prevents_change(self):
        with patch.object(i, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            with self.assertRaisesRegex(RuntimeError, 'second_transport_active'):
                e.plan(i, TARGET, self.caps)

    def test_success_preserves_cursors_profiles_units_and_wordpress_errors(self):
        before = {p: p.read_bytes() for p in self.originals if p != self.path}
        cursor = self.root/'file-checkpoint'; cursor.write_text('latest offset')
        before[cursor] = cursor.read_bytes()
        original = json.loads(self.path.read_text())
        with patch.object(i, 'execute', return_value=subprocess.CompletedProcess([], 0, '', '')) as execute:
            e.migrate(i, TARGET, self.caps, apply=True)
        self.assertEqual(json.loads(self.path.read_text()), e.prepare(original))
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        meta = json.loads((i.BASE/'manifest.json').read_text())
        self.assertEqual(meta['monitoring_policy'], e.POLICY)
        self.assertEqual(meta['resource_sampling'], e.SAMPLING)
        self.assertEqual(meta['services'], self.meta['services'])
        self.assertFalse(meta.get('storage_observer', False))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        calls = [call.args[0] for call in execute.call_args_list]
        transport = self.meta['services'][0]
        self.assertIn(['systemctl', 'stop', transport], calls)
        self.assertIn(['systemctl', 'start', transport], calls)
        self.assertFalse(any('duplika-betterstack-cloudlinux.service' in args for args in calls))
        legacy_upgrade.validate(i, meta, TARGET)
        with patch.object(i, 'backup') as backup, contextlib.redirect_stdout(io.StringIO()):
            e.migrate(i, TARGET, self.caps, apply=True)
        backup.assert_not_called()

    def test_failed_activation_restores_config_and_metadata_not_advanced_cursors(self):
        originals = {p: p.read_bytes() for p in [self.path, *i.runtime_files(), i.BASE/'manifest.json', i.BASE/'complete']}
        cursor = self.root/'checkpoint'; cursor.write_text('before')
        def fail(args, **kwargs):
            if args[:3] == ['runuser', '-u', 'vector']:
                cursor.write_text('advanced')
                raise RuntimeError('native_validation_failed')
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(i, 'execute', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'native_validation_failed'):
                e.migrate(i, TARGET, self.caps, apply=True)
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(cursor.read_text(), 'advanced')


class MinimalMigrationTests(LegacyMigrationTests):
    variant = 'almalinux-wordpress-minimal-v4'
    release = '4.0.0-rc24'
