import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import installer


def result(out='', code=0):
    return subprocess.CompletedProcess([], code, out, '')


class PermissionTests(unittest.TestCase):
    def test_real_transport_group_can_read_replacement_under_umask_077(self):
        import pwd
        account = pwd.getpwnam('daemon')
        with tempfile.TemporaryDirectory(dir='/var/lib') as directory:
            root = Path(directory)
            try:
                os.chown(root, 0, account.pw_gid)
            except OSError as error:
                if error.errno == 22:
                    self.skipTest('executor maps only uid/gid 0; exercised in CI')
                raise
            root.chmod(0o750)
            path = root / 'config.json'; path.write_bytes(b'{}')
            os.chown(path, 0, account.pw_gid)
            old_mask = os.umask(0o077)
            try:
                installer.write(path, b'{}', 0o640)
            finally:
                os.umask(old_mask)
            readable = subprocess.run(['runuser', '-u', account.pw_name, '--', 'test', '-r', str(path)])
            self.assertEqual(readable.returncode, 0)
            writable = subprocess.run(['runuser', '-u', account.pw_name, '--', 'test', '-w', str(path)])
            self.assertNotEqual(writable.returncode, 0)

    def test_exact_modes_with_restrictive_umask_new_replace_and_restore(self):
        with tempfile.TemporaryDirectory(dir='/root') as directory:
            root = Path(directory); state = root / 'state'; state.mkdir()
            old_mask = os.umask(0o077)
            try:
                for mode in (0o600, 0o640, 0o644, 0o700):
                    path = root / ('file-' + str(mode))
                    installer.write(path, b'original', mode)
                    self.assertEqual(path.stat().st_mode & 0o777, mode)
                    with patch.object(installer, 'STATE', state):
                        record = installer.backup([path], [])
                    installer.write(path, b'changed', 0o600)
                    with patch.object(installer, 'execute'), patch.object(installer, 'trusted_executable', return_value=None):
                        installer.restore(record)
                    self.assertEqual(path.read_bytes(), b'original')
                    self.assertEqual(path.stat().st_mode & 0o777, mode)
            finally:
                os.umask(old_mask)


class RestoreTests(unittest.TestCase):
    def record(self):
        return {'files': {}, 'services': {
            'new.timer': {'active': False, 'enabled': False},
            'other.timer': {'active': True, 'enabled': True},
            'vector.service': {'active': True, 'enabled': True}}}

    def service_result(self, args, **kwargs):
        if 'LoadState' in args:
            return result('not-found\n' if 'new.timer' in args else 'loaded\n')
        return result()

    def test_missing_new_unit_does_not_abort_transport_recovery(self):
        calls = []
        def execute(args):
            calls.append(args)
            if 'new.timer' in args:
                raise AssertionError('missing unit must not be enabled/disabled')
            return result()
        with patch.object(installer, 'run', side_effect=self.service_result), patch.object(installer, 'execute', side_effect=execute):
            installer.restore(self.record())
        self.assertIn(['systemctl', 'start', 'vector.service'], calls)
        first_enable = next(i for i, args in enumerate(calls) if args[1] == 'enable')
        self.assertLess(calls.index(['systemctl', 'start', 'vector.service']), first_enable)

    def test_unrelated_restart_failure_still_attempts_vector_and_reports_failure(self):
        calls = []
        def execute(args):
            calls.append(args)
            if args == ['systemctl', 'start', 'other.timer']:
                raise RuntimeError('step_failed:systemctl:start:other.timer:exit=1')
            return result()
        with patch.object(installer, 'run', side_effect=self.service_result), patch.object(installer, 'execute', side_effect=execute):
            with self.assertRaisesRegex(RuntimeError, 'rollback_incomplete:.*other.timer'):
                installer.restore(self.record())
        self.assertIn(['systemctl', 'start', 'vector.service'], calls)

    def test_unrelated_enable_failure_occurs_after_vector_restart(self):
        calls = []
        def execute(args):
            calls.append(args)
            if args == ['systemctl', 'enable', 'other.timer']:
                raise RuntimeError('enable_failed')
            return result()
        with patch.object(installer, 'run', side_effect=self.service_result), patch.object(installer, 'execute', side_effect=execute):
            with self.assertRaisesRegex(RuntimeError, 'rollback_incomplete:enable_failed'):
                installer.restore(self.record())
        self.assertLess(calls.index(['systemctl', 'start', 'vector.service']), calls.index(['systemctl', 'enable', 'other.timer']))

    def test_inactive_transport_is_reported_even_if_start_returns_success(self):
        def run(args, **kwargs):
            if args == ['systemctl', 'is-active', '--quiet', 'vector.service']:
                return result(code=3)
            return self.service_result(args)
        with patch.object(installer, 'run', side_effect=run), patch.object(installer, 'execute', return_value=result()):
            with self.assertRaisesRegex(RuntimeError, 'restore_service_inactive:vector.service'):
                installer.restore(self.record())

    def test_original_failure_is_preserved_when_rollback_fails(self):
        with patch.object(installer, 'restore', side_effect=RuntimeError('rollback_incomplete:fixture')):
            with self.assertRaisesRegex(RuntimeError, 'original_failure; rollback_incomplete:fixture'):
                installer.recover_failure({}, RuntimeError('original_failure'))

    def test_transport_stopping_within_its_unit_budget_is_not_a_probe_timeout(self):
        def run(args, timeout=20):
            if args == ['systemctl', 'stop', 'vector.service'] and timeout < 30:
                raise RuntimeError('timeout:systemctl')
            return self.service_result(args)
        with patch.object(installer, 'run', side_effect=run), patch.object(installer, 'execute', return_value=result()):
            installer.restore(self.record())

    def test_loaded_unit_stop_failure_is_reported_and_recovery_continues(self):
        def run(args, **kwargs):
            if args == ['systemctl', 'stop', 'vector.service']:
                return result(code=1)
            return self.service_result(args)
        with patch.object(installer, 'run', side_effect=run), patch.object(installer, 'execute', return_value=result()) as execute:
            with self.assertRaisesRegex(RuntimeError, 'restore_stop_failed:vector.service:exit=1'):
                installer.restore(self.record())
        self.assertIn(__import__('unittest').mock.call(['systemctl', 'start', 'vector.service']), execute.call_args_list)

    def test_absent_new_unit_does_not_make_rollback_incomplete(self):
        def run(args, **kwargs):
            if args == ['systemctl', 'stop', 'new.timer']:
                return result(code=5)
            return self.service_result(args)
        with patch.object(installer, 'run', side_effect=run), patch.object(installer, 'execute', return_value=result()):
            installer.restore(self.record())

    def test_systemctl_error_names_operation_unit_without_private_output(self):
        with patch.object(installer, 'run', return_value=result('private diagnostic', 1)):
            with self.assertRaisesRegex(RuntimeError, '^step_failed:systemctl:start:vector.service:exit=1$'):
                installer.execute(['systemctl', 'start', 'vector.service'])


class UnitLocationTests(unittest.TestCase):
    def test_vendor_fragment_and_local_dropin_are_both_verified(self):
        with tempfile.TemporaryDirectory(dir='/root') as directory:
            root = Path(directory)
            vendor = root / 'vendor.service'; vendor.write_text('vendor')
            dropin = root / '30-local.conf'; dropin.write_text('drop-in')
            props = 'FragmentPath=' + str(vendor) + '\nDropInPaths=' + str(dropin) + '\n'
            with patch.object(installer, 'execute', return_value=result(props)):
                self.assertEqual(installer.unit_files('vector.service'), [vendor, dropin])
            vendor.unlink()
            with patch.object(installer, 'execute', return_value=result(props)):
                with self.assertRaises(FileNotFoundError):
                    installer.unit_files('vector.service')

    def test_unknown_or_relative_fragment_rejected(self):
        for props in ('FragmentPath=\n', 'FragmentPath=relative.service\n'):
            with patch.object(installer, 'execute', return_value=result(props)):
                with self.assertRaisesRegex(RuntimeError, 'unit_fragment_missing'):
                    installer.unit_files('vector.service')


if __name__ == '__main__':
    unittest.main()
