import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import installer
import identity

TARGET={'host':'cloud.example.net','server_ip':'203.0.113.20','source_id':1234568,
        'ingesting_host':'s1234568.us-west-2a.betterstackdata.com'}

class ConfigurationTests(unittest.TestCase):
    def args(self,path):
        return types.SimpleNamespace(target=path,source_id=TARGET['source_id'],
            ingesting_host=TARGET['ingesting_host'],server_ip=TARGET['server_ip'],host=TARGET['host'])
    def test_configuration_has_no_token_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir='/root') as d:
            p=Path(d)/'profile.json'
            with patch.object(installer,'secure_target',return_value={}):
                installer.create_configuration(self.args(p));before=p.stat().st_mtime_ns
                installer.create_configuration(self.args(p))
            self.assertEqual(json.loads(p.read_text()),TARGET)
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            self.assertEqual(p.stat().st_mtime_ns,before)
    def test_configuration_never_overwrites_another_target(self):
        with tempfile.TemporaryDirectory(dir='/root') as d:
            p=Path(d)/'profile.json';p.write_text(json.dumps(dict(TARGET,source_id=1)))
            with patch.object(installer,'secure_target',return_value={}):
                with self.assertRaisesRegex(RuntimeError,'existing_profile_differs'):installer.create_configuration(self.args(p))
            self.assertEqual(json.loads(p.read_text())['source_id'],1)
    def test_credentials_require_tty(self):
        with patch.object(installer.sys.stdin,'isatty',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'interactive_terminal'):installer.create_credential(TARGET,Path('/root/nonexistent-test-source.json'))
    def test_secret_is_not_printed_or_in_arguments(self):
        with tempfile.TemporaryDirectory(dir='/root') as d:
            p=Path(d)/'source.json'
            with patch.object(installer.sys.stdin,'isatty',return_value=True),patch('getpass.getpass',return_value='TEST_SECRET'),patch('builtins.print') as output:
                installer.create_credential(TARGET,p)
            self.assertEqual(json.loads(p.read_text())['token'],'TEST_SECRET')
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            self.assertNotIn('TEST_SECRET',str(output.call_args_list))

class PackagingTests(unittest.TestCase):
    def test_gzip_header_is_normalized_across_python_variants(self):
        import base64
        import importlib.util
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('public_builder',root/'scripts/build.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        original=builder.gzip.compress
        with tempfile.TemporaryDirectory() as directory:
            variants=[]
            for os_byte in (3,255):
                def compress(data,mtime=0):
                    value=original(data,mtime=mtime)
                    return value[:9]+bytes([os_byte])+value[10:]
                output=Path(directory)/str(os_byte)
                with patch.object(builder.gzip,'compress',side_effect=compress):
                    variants.append(builder.build(output))
                body=(output/'installer.sh').read_text()
                raw=base64.b64decode(body.split("<<'DUPLIKA_4_PAYLOAD'\n",1)[1].split('DUPLIKA_4_PAYLOAD',1)[0])
                self.assertEqual(raw[9],255)
            self.assertEqual(variants[0],variants[1])

if __name__=='__main__':unittest.main()
