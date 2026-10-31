"""Reproduce root:root 0664 data without widening executable trust."""
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import patch
import health as h


class IniModeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/root');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)/'root'
        (self.base/'etc/php.d').mkdir(parents=True)
        self.ini=self.base/'etc/php.d/zzzzzzz-pecl.ini'
        self.ini.write_text('extension="timezonedb.so"\n')
        self.ini.chmod(0o664)

    def test_root_group_writable_ini_is_read_without_changing_permissions(self):
        before=self.ini.stat()
        self.assertIn('timezonedb.so',h.ea_ini_text(self.ini,self.base))
        self.assertEqual(self.ini.stat().st_mode,before.st_mode)
        self.assertEqual(self.ini.stat().st_mtime_ns,before.st_mtime_ns)

    def test_world_write_and_directory_group_write_remain_rejected(self):
        self.ini.chmod(0o666)
        with self.assertRaises(ValueError):h.ea_ini_text(self.ini,self.base)
        self.ini.chmod(0o664);self.ini.parent.chmod(0o775)
        with self.assertRaises(ValueError):h.ea_ini_text(self.ini,self.base)

    def test_other_writable_group_is_rejected_on_open_descriptor(self):
        real=os.fstat
        def bad_group(fd):
            info=real(fd)
            return types.SimpleNamespace(st_uid=0,st_gid=12345,st_mode=info.st_mode,st_size=info.st_size)
        with patch('os.fstat',side_effect=bad_group):
            with self.assertRaises(ValueError):h.ea_ini_text(self.ini,self.base)

    def test_other_owner_is_rejected_on_open_descriptor(self):
        real=os.fstat
        def bad_owner(fd):
            info=real(fd)
            return types.SimpleNamespace(st_uid=12345,st_gid=0,st_mode=info.st_mode,st_size=info.st_size)
        with patch('os.fstat',side_effect=bad_owner):
            with self.assertRaises(ValueError):h.ea_ini_text(self.ini,self.base)

    def test_fifo_does_not_block_and_large_ini_is_rejected(self):
        self.ini.unlink();os.mkfifo(self.ini)
        with self.assertRaises(ValueError):h.ea_ini_text(self.ini,self.base)
        self.ini.unlink();self.ini.write_bytes(b'x'*262145)
        with self.assertRaises(ValueError):h.ea_ini_text(self.ini,self.base)

    def test_root_symlink_to_group_writable_ini_inside_runtime_is_read(self):
        link=self.base/'etc/php.ini';link.symlink_to(self.ini)
        self.assertIn('timezonedb.so',h.ea_ini_text(link,self.base))
