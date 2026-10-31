"""lsadm vendor metadata is data only; executable trust stays root-only."""
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import patch

import health as h


class VendorMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.folder = self.home/'autoupdate'
        self.folder.mkdir(mode=0o700)
        self.release = self.folder/'release'
        self.release.write_text('6.3.7-ent\n')
        self.release.chmod(0o644)

    def test_root_metadata_and_lsadm_metadata_are_read_as_data(self):
        self.assertEqual(h.vendor_release(self.home), '6.3.7-ent')
        real = os.fstat
        def vendor_stat(fd):
            value = real(fd)
            return types.SimpleNamespace(st_uid=12345, st_mode=value.st_mode, st_size=value.st_size)
        with patch('pwd.getpwnam', return_value=types.SimpleNamespace(pw_uid=12345)), \
             patch('os.fstat', side_effect=vendor_stat):
            self.assertEqual(h.vendor_release(self.home), '6.3.7-ent')

    def test_missing_account_still_accepts_root(self):
        with patch('pwd.getpwnam', side_effect=KeyError):
            self.assertEqual(h.vendor_release(self.home), '6.3.7-ent')

    def test_directory_or_file_writable_by_others_are_rejected(self):
        for path in (self.folder, self.release):
            original=path.stat().st_mode & 0o777
            path.chmod(0o777)
            with self.assertRaises(ValueError): h.vendor_release(self.home)
            path.chmod(original)

    def test_wrong_owner_is_rejected(self):
        real=os.fstat
        def wrong(fd):
            value=real(fd)
            return types.SimpleNamespace(st_uid=98765, st_mode=value.st_mode, st_size=value.st_size)
        with patch('pwd.getpwnam', return_value=types.SimpleNamespace(pw_uid=12345)), patch('os.fstat', side_effect=wrong):
            with self.assertRaises(ValueError): h.vendor_release(self.home)

    def test_symlink_file_directory_and_fifo_are_not_followed(self):
        self.release.unlink()
        target=self.home/'target';target.write_text('6.3.7-ent')
        self.release.symlink_to(target)
        with self.assertRaises(OSError): h.vendor_release(self.home)
        self.release.unlink();os.mkfifo(self.release)
        with self.assertRaises(ValueError): h.vendor_release(self.home)
        self.release.unlink();self.folder.rmdir()
        self.folder.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(OSError): h.vendor_release(self.home)

    def test_size_cap(self):
        self.release.write_text('x'*257)
        with self.assertRaises(ValueError): h.vendor_release(self.home)

    def test_vendor_metadata_does_not_relax_executable_trust(self):
        with patch.object(h, 'maintenance_text', return_value='LSWS_HOME=/usr/local/lsws'), \
             patch.object(h.Path, 'exists', return_value=True), \
             patch.object(h, 'trusted_executable', side_effect=RuntimeError('unsafe_executable')):
            with self.assertRaises(RuntimeError): h.litespeed_home(lambda p:b'')

    def test_stage_failure_has_no_raw_exception_data(self):
        with patch.object(h, 'litespeed_home', return_value=self.home), \
             patch.object(h, 'maintenance_text', side_effect=['6.3.7','7']), \
             patch.object(h, 'vendor_release', side_effect=PermissionError('PRIVATE_SECRET')):
            value=h.probe_litespeed(lambda p:b'', lambda u: self.fail('network'))
        self.assertEqual(value['stage'], 'enterprise_marker')
        self.assertEqual(value['failure_kind'], 'permission_denied')
        self.assertEqual(value['server']['installed'], '6.3.7')
        self.assertNotIn('PRIVATE_SECRET', json.dumps(value))

    def test_old_failed_cache_is_rechecked_on_new_probe_schema(self):
        old={'checked_at':1000,'observation':{'status':'unknown'}}
        written=[]
        with patch.object(h, 'probe_litespeed', return_value={'status':'installed'}) as probe:
            value=h.daily_litespeed(self.home,lambda p:json.dumps(old).encode(),lambda *args:written.append(args),1001,online=True)
        probe.assert_called_once()
        self.assertEqual(value['probe_schema'],h.MAINTENANCE_SCHEMA)
        self.assertEqual(len(written),1)
