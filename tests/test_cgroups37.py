"""Monthly cgroups publication and migration from the standalone pilot."""
import datetime
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import identity
import installer
import test_upgrade_rc15

UTC = datetime.timezone.utc
TARGET = test_upgrade_rc15.TARGET
CAPS = {'os_id': 'cloudlinux', 'os_version': '8.10'}
META = {'target': TARGET, 'capabilities': CAPS}


class MonthlyInventoryTests(unittest.TestCase):
    def test_filesystem_classification_and_module_tag(self):
        at = datetime.datetime(2026, 12, 1, tzinfo=UTC)
        for fs, mode, loaded in (('cgroup2fs', 'v2', True), ('tmpfs', 'no_unificado', False),
                                 ('sysfs', 'desconocido', False)):
            with self.subTest(fs=fs):
                with patch.object(identity.health, 'run',
                        return_value=subprocess.CompletedProcess([], 0, fs + '\n', '')) as run, \
                        patch.object(Path, 'is_dir', autospec=True,
                                     side_effect=lambda path: loaded and path.name == 'kmodlve'), \
                        patch.object(Path, 'open', side_effect=FileNotFoundError):
                    row = identity.cgroups_sample(META, at)
                run.assert_called_once_with(['/usr/bin/stat', '-fc', '%T', '/sys/fs/cgroup/'], timeout=5)
                self.assertEqual(row['tags']['mode'], mode)
                self.assertEqual(row['tags']['lve_loaded'], 'yes' if loaded else 'no')
                self.assertEqual(row['tags']['root_fs'], fs)
                self.assertEqual(row['name'], 'duplika_cloudlinux_cgroups_info')

    def test_verify_is_read_only_and_retry_reuses_the_sample_until_month_changes(self):
        start = datetime.datetime(2026, 11, 30, 22, tzinfo=UTC)
        hour = datetime.timedelta(hours=1)
        uri = 'https://' + TARGET['ingesting_host'] + '/'
        def sample(meta, at):
            return {'dt': at.isoformat()}
        with tempfile.TemporaryDirectory(dir='/root') as directory, \
                patch.object(identity, 'STATE', Path(directory)), \
                patch.object(identity, 'cgroups_sample', side_effect=sample) as probe, \
                patch.object(identity, 'send', side_effect=[RuntimeError('failed'), None, None]) as send:
            observe = lambda publish, at: identity.cgroups_inventory(META, uri, 'TEST_SECRET', publish, at)
            self.assertEqual(observe(False, start)['status'], 'not_sampled')
            probe.assert_not_called()
            self.assertEqual(observe(True, start)['status'], 'delivery_pending')
            self.assertEqual(observe(True, start + hour)['status'], 'sent')
            self.assertEqual(observe(True, start + hour)['status'], 'sent')
            self.assertEqual(send.call_args_list[0], send.call_args_list[1])
            self.assertEqual(probe.call_count, 1)
            new_month = observe(True, start + 2 * hour)
            self.assertEqual(new_month['period_utc'], '2026-12')
            self.assertNotEqual(send.call_args_list[0], send.call_args_list[2])
            self.assertEqual(probe.call_count, 2)

    def test_failed_probe_is_visible_and_leaves_no_receipt(self):
        at = datetime.datetime(2026, 12, 1, tzinfo=UTC)
        with tempfile.TemporaryDirectory(dir='/root') as directory, \
                patch.object(identity, 'STATE', Path(directory)), \
                patch.object(identity.health, 'run',
                             return_value=subprocess.CompletedProcess([], 1, '', '')), \
                patch.object(identity, 'send') as send:
            result = identity.cgroups_inventory(META, '', 'TEST_SECRET', True, at)
            self.assertEqual(result['status'], 'sample_unavailable')
            self.assertFalse(list(Path(directory).iterdir()))
            send.assert_not_called()


class ReviewedRc36Tests(test_upgrade_rc15.UpgradeTests):
    previous_version = '4.0.0-rc36'


class PilotTransactionTests(unittest.TestCase):
    def test_failed_upgrade_restores_pilot_files_and_timer(self):
        with tempfile.TemporaryDirectory(dir='/root') as directory:
            root = Path(directory)
            units = root / 'units'
            units.mkdir()
            timer = installer.CGROUPS_PILOT + '.timer'
            paths = [root / 'pilot.py', units / (installer.CGROUPS_PILOT + '.service'), units / timer]
            for path in paths:
                path.write_text(path.name)
            inactive = subprocess.CompletedProcess([], 3, 'loaded', '')
            active = subprocess.CompletedProcess([], 0, 'loaded', '')
            def run(args, **kwargs):
                return active if args[-1] == timer or args[1] == 'show' else inactive
            with patch.object(installer, 'STATE', root), \
                    patch.object(installer, 'cgroups_pilot', return_value=paths), \
                    patch.object(installer, 'run', side_effect=run), \
                    patch.object(installer, 'execute') as execute, \
                    patch.object(installer, 'trusted_executable', return_value=None):
                with self.assertRaisesRegex(RuntimeError, 'activation_failed'):
                    with installer.upgrade_transaction([], [], TARGET, CAPS):
                        self.assertTrue(all(not p.exists() for p in paths))
                        raise RuntimeError('activation_failed')
            self.assertTrue(all(p.read_text() == p.name for p in paths))
            execute.assert_any_call(['systemctl', 'disable', '--now', timer])
            execute.assert_any_call(['systemctl', 'start', timer])
            execute.assert_any_call(['systemctl', 'enable', timer])
