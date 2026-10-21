import contextlib
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import capabilities
import context_cleanup
import identity
import installer

ROOT = Path(__file__).resolve().parents[1]
TARGET = {'host': 'cloud.example.net', 'server_ip': '203.0.113.20', 'source_id': 1234568,
          'ingesting_host': 's1234568.us-west-2a.betterstackdata.com'}


def fixture():
    spec = importlib.util.spec_from_file_location('context_reference', ROOT / 'src/legacy/legacy_context_reference.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    profile = {'host': TARGET['host'], 'site': 'example.net', 'docroot': '/home/account/public_html',
               'since': '2026-01-01T00:00:00+00:00', 'context_since': '2026-01-01T00:00:00+00:00',
               'interval': 60}
    return module.make_config(profile, 'TEST_SECRET', TARGET['ingesting_host'])


class CleanupTests(unittest.TestCase):
    def test_preserves_metrics_destinations_logs_and_cursors(self):
        original = fixture()
        before = copy.deepcopy(original)
        changed = context_cleanup.prepare(original)
        self.assertEqual(original, before)
        self.assertEqual(changed['data_dir'], original['data_dir'])
        self.assertEqual(changed['sinks'], original['sinks'])
        for name, value in original['sources'].items():
            if name not in ('duplika_journal', 'duplika_server_context'):
                self.assertEqual(changed['sources'][name], value)
        for name in ('host_to_json', 'host_for_betterstack', 'normalize_errors', 'useful_errors'):
            if name != 'normalize_errors':
                self.assertEqual(changed['transforms'][name], original['transforms'][name])
        self.assertNotIn('duplika_server_context', changed['sources'])
        journal = changed['sources']['duplika_journal']
        self.assertNotIn('crond.service', journal['include_matches']['_SYSTEMD_UNIT'])
        self.assertNotIn('SYSLOG_IDENTIFIER=CROND', journal['extra_args'])
        self.assertNotIn('selected_cron', changed['transforms']['tag_journal']['source'])

    def test_native_test_failure_removes_private_config(self):
        paths = []
        def fail(args):
            path = Path(args[-1]); paths.append(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            raise RuntimeError('native_failure')
        with self.assertRaisesRegex(RuntimeError, 'native_failure'):
            context_cleanup.validate(context_cleanup.prepare(fixture()), '/usr/bin/vector', fail)
        self.assertFalse(paths[0].exists())
        self.assertFalse(paths[0].parent.exists())

    def test_almalinux8_detected_cloudlinux8_requires_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'os-release'
            path.write_text('ID=almalinux\nVERSION_ID="8.10"\n')
            self.assertEqual(capabilities.detect(path)['os_version'], '8.10')
            path.write_text('ID=cloudlinux\nVERSION_ID="8.10"\n')
            with self.assertRaisesRegex(RuntimeError, 'edition_detector_missing'):
                capabilities.detect(path, finder=lambda name: None)

    def test_new_almalinux8_install_refused_before_writes(self):
        with patch.object(installer, 'write') as write:
            with self.assertRaisesRegex(RuntimeError, 'reviewed_migration_only'):
                installer.fresh(TARGET, {'os_id': 'almalinux', 'os_version': '8.10'}, Path('/missing'))
            write.assert_not_called()


class CleanupTransactionTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root')))
        for name in ('BASE', 'STATE', 'UNITS', 'LEGACY'):
            path = self.root / name; path.mkdir()
            self.stack.enter_context(patch.object(installer, name, path))
        self.stack.enter_context(patch.object(identity, 'BASE', installer.BASE))
        self.stack.enter_context(patch.object(identity, 'STATE', installer.STATE))
        self.config = self.root / 'vector.yaml'; self.config.write_text(json.dumps(fixture()))
        self.config.chmod(0o640)
        # Configuration is root-owned but readable by a distinct transport group.
        import os
        self.transport_gid = 1
        try:
            os.chown(self.config, 0, self.transport_gid)
        except OSError as error:
            if error.errno != 22:
                raise
            # Local executor maps only gid 0; the CI root fixture exercises gid 1.
            self.transport_gid = 0
        self.profile = self.root / 'profile.json'; self.profile.write_text('{}')
        self.helper = self.root / 'context.py'; self.helper.write_text('reviewed old helper')
        self.removed = [self.helper, installer.UNITS / 'duplika-vector-context.service',
                        installer.UNITS / 'duplika-vector-context.timer']
        names = ['vector.service', 'duplika-vector-acl.timer', 'duplika-vector-acl.service',
                 'duplika-vector-context.timer', 'duplika-vector-context.service']
        for name in names:
            (installer.UNITS / name).write_text('reviewed unit ' + name)
        vendor = self.root / 'vendor'; vendor.mkdir()
        self.vendor_unit = vendor / 'vector.service'
        (installer.UNITS / 'vector.service').replace(self.vendor_unit)
        self.dropin = installer.UNITS / '30-journal.conf'; self.dropin.write_text('reviewed drop-in')
        self.stack.enter_context(patch.object(installer, 'unit_files', side_effect=lambda name:
            [self.vendor_unit, self.dropin] if name == 'vector.service' else [installer.UNITS / name]))
        spec = {'base_variant': 'almalinux-wordpress-context-r6', 'config': str(self.config),
                'profile': str(self.profile), 'runtime_files': [str(self.helper)],
                'vector_service': 'vector.service',
                'services': ['vector.service', 'duplika-vector-acl.timer', 'duplika-vector-context.timer']}
        self.old = {'spec': spec}
        self.stack.enter_context(patch.object(installer, 'validate_migration', return_value=self.old))
        self.stack.enter_context(patch.object(context_cleanup, 'paths', return_value=self.removed))
        self.validate = self.stack.enter_context(patch.object(context_cleanup, 'validate'))
        self.execute = self.stack.enter_context(patch.object(installer, 'execute', return_value=subprocess.CompletedProcess([], 0, '', '')))
        self.stack.enter_context(patch.object(installer, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')))
        self.stack.enter_context(patch.object(installer, 'trusted_executable', side_effect=lambda name: name if name.startswith('/') else '/usr/bin/' + name))
        self.status = self.stack.enter_context(patch.object(identity, 'status', return_value={}))
        self.caps = {'os_id': 'almalinux', 'os_version': '8.10'}
        self.checkpoint = self.root / 'journal-cursor'; self.checkpoint.write_text('keep position')
        self.relay = self.root / 'context.jsonl'; self.relay.write_text('keep history')
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)

    def test_explicit_cleanup_and_selected_runtime(self):
        installer.upgrade(TARGET, self.caps, simplify_context=True)
        self.assertTrue(all(not path.exists() for path in self.removed))
        self.assertEqual(self.config.stat().st_gid, self.transport_gid)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        meta = installer.load(installer.BASE / 'manifest.json')
        self.assertEqual(meta['pipeline_variant'], 'almalinux-wordpress-minimal-v4')
        self.assertNotIn('duplika-vector-context.timer', meta['services'])
        self.assertNotIn(str(self.helper), meta['critical_paths'])
        self.assertIn(str(self.vendor_unit), meta['critical_paths'])
        self.assertIn(str(self.dropin), meta['critical_paths'])
        self.assertNotIn(str(installer.UNITS / 'vector.service'), meta['critical_paths'])
        unit = (installer.UNITS / (installer.NAME + '.service')).read_text()
        self.assertIn('ExecStart=' + sys.executable + ' -E -s -B ', unit)
        self.assertEqual(self.checkpoint.read_text(), 'keep position')
        self.assertEqual(self.relay.read_text(), 'keep history')
        installer.upgrade(TARGET, self.caps, simplify_context=True)

    def test_failed_activation_restores_config_group_and_removed_files(self):
        before = {path: path.read_bytes() for path in [self.config, *self.removed]}
        self.status.side_effect = RuntimeError('activation failed')
        with self.assertRaisesRegex(RuntimeError, 'activation failed'):
            installer.upgrade(TARGET, self.caps, simplify_context=True)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(self.config.stat().st_gid, self.transport_gid)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.checkpoint.read_text(), 'keep position')
        self.assertEqual(self.relay.read_text(), 'keep history')

    def test_failed_native_check_precedes_backup_and_service_changes(self):
        self.validate.side_effect = RuntimeError('native failure')
        with self.assertRaisesRegex(RuntimeError, 'native failure'):
            installer.upgrade(TARGET, self.caps, simplify_context=True)
        self.execute.assert_not_called()
        self.assertFalse((installer.STATE / 'last-transaction.json').exists())

    def test_missing_unit_detected_before_backup_or_service_changes(self):
        with patch.object(installer, 'unit_files', side_effect=FileNotFoundError('vendor unit')):
            with self.assertRaises(FileNotFoundError):
                installer.upgrade(TARGET, self.caps, simplify_context=True)
        self.execute.assert_not_called()
        self.assertFalse((installer.STATE / 'last-transaction.json').exists())
        self.assertTrue(self.helper.exists())

    def test_cleanup_not_implicitly_applied_and_wrong_variant_rejected(self):
        self.old['spec']['base_variant'] = 'almalinux-wordpress-v2'
        with self.assertRaisesRegex(RuntimeError, 'requires_reviewed_context'):
            installer.upgrade(TARGET, self.caps, simplify_context=True)
        self.validate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
