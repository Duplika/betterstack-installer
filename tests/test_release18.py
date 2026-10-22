"""Release transitions, safe retries, and inherited pipeline preservation."""
import contextlib
import ast
import base64
import tarfile
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch
import identity
import installer as i
import legacy_upgrade as legacy
from test_upgrade_rc15 import UpgradeTests, TARGET


class CurrentReleaseTests(UpgradeTests):
    def install_current(self):
        for path in i.runtime_files(): path.write_bytes((i.HERE / path.name).read_bytes())
        (i.CLOUD / 'collector.py').write_bytes((i.HERE / 'collector.py').read_bytes())
        self.meta.update(agent_version=i.VERSION, installer_version=i.VERSION,
            resource_sampling={'lve_seconds': 300, 'processes_seconds': 300})
        self.meta['baseline'] = identity.snapshot(self.meta['critical_paths'], self.meta['config_path'])
        (i.BASE / 'manifest.json').write_text(json.dumps(self.meta))
        (i.BASE / 'complete').write_text(i.VERSION + '\n')

    def test_repeated_upgrade_checks_health_without_writes_or_backup(self):
        self.install_current()
        before = {p:p.read_bytes() for p in i.runtime_files()+[i.BASE/'manifest.json',i.CLOUD/'vector.json']}
        with patch.object(identity, 'status', return_value={}) as status, patch.object(i, 'backup') as backup:
            i.upgrade(TARGET, {})
        status.assert_called_once(); backup.assert_not_called()
        self.assertEqual(before, {p:p.read_bytes() for p in before})

    def test_repeat_rejects_rebaselined_altered_code_and_capability_changes(self):
        self.install_current()
        with self.assertRaisesRegex(RuntimeError, 'current_release_mismatch'):
            i.validate_current(TARGET, {'os_id':'changed'})
        (i.BASE/'health.py').write_text('changed but rebaselined')
        self.meta['baseline'] = identity.snapshot(self.meta['critical_paths'], self.meta['config_path'])
        (i.BASE/'manifest.json').write_text(json.dumps(self.meta))
        with self.assertRaisesRegex(RuntimeError, 'current_runtime_differs_from_release'):
            i.validate_current(TARGET, {})

    def test_repeat_unhealthy_is_not_reported_as_already_updated(self):
        self.install_current()
        with patch.object(identity, 'status', side_effect=RuntimeError('integrity_or_service_failure')):
            with self.assertRaisesRegex(RuntimeError, 'service_failure'): i.upgrade(TARGET, {})


class PublicationTests(unittest.TestCase):
    def test_publisher_uses_existing_sandbox_unit_without_credentials(self):
        with patch.object(i, 'run', return_value=subprocess.CompletedProcess([],0)) as run, contextlib.redirect_stdout(io.StringIO()) as output:
            i.request_publication(TARGET)
        self.assertEqual(run.call_args.args[0], ['systemctl','start',i.NAME+'.service'])
        event=json.loads(output.getvalue());self.assertFalse(event['metrics_delivery_verified'])
        self.assertEqual(event['identity_publication'],'completed_or_unchanged')

    def test_failure_or_timeout_keeps_local_install_and_reports_hourly_retry(self):
        for response in (subprocess.CompletedProcess([],1), RuntimeError('private diagnostic')):
            with self.subTest(response=response):
                options={'side_effect':response} if isinstance(response,Exception) else {'return_value':response}
                with patch.object(i,'run',**options), patch.object(i,'restore') as restore, contextlib.redirect_stdout(io.StringIO()) as output:
                    i.request_publication(TARGET)
                restore.assert_not_called()
                self.assertEqual(json.loads(output.getvalue())['identity_publication'],'pending_hourly_retry')
                self.assertNotIn('private diagnostic', output.getvalue())


class LegacyTests(UpgradeTests):
    variant = 'almalinux-wordpress-v2'

    def setUp(self):
        super().setUp()
        version, _, _, _, blueprint, reference, services = legacy.LAYOUTS[self.variant]
        config=str(self.root/'legacy-vector.yaml');profile=str(self.root/'legacy-profile.json');helper=str(self.root/'acl.py')
        for source in (Path(__file__).parents[1]/'src/legacy').iterdir():
            if source.is_file(): (i.HERE/source.name).write_bytes(source.read_bytes())
        self.stack.enter_context(patch.dict(legacy.LAYOUTS, {self.variant:(version,config,profile,helper,blueprint,reference,services)}))
        data={'host':TARGET['host'],'site':'example.net','docroot':'/home/account/public_html',
              'since':'2026-01-01T00:00:00+00:00','context_since':'2026-01-01T00:00:00+00:00','interval':60}
        spec=importlib.util.spec_from_file_location('fixture_blueprint',i.HERE/blueprint)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        cfg=module.make_config(data,'TEST_SECRET',TARGET['ingesting_host'])
        if self.variant.endswith('minimal-v4'): cfg=i.context_cleanup.prepare(cfg)
        Path(config).write_text(json.dumps(cfg));Path(profile).write_text(json.dumps(data))
        Path(helper).write_bytes((i.HERE/reference).read_bytes())
        unit_files={}
        for name in services+[services[1][:-6]+'.service']:
            p=self.root/('vendor-'+name);p.write_text('reviewed unit fixture');unit_files[name]=[p]
        self.stack.enter_context(patch.object(i,'unit_files',side_effect=lambda name:unit_files[name]))
        originals=[Path(helper),*[p for group in unit_files.values() for p in group],Path(config),Path(profile)]
        self.meta.update(agent_version=version,installer_version=version,config_path=config,profile_path=profile,
            pipeline_variant=self.variant,services=services+[i.NAME+'.timer'],oneshots=[services[1][:-6]+'.service'])
        self.meta.pop('storage_observer')
        self.meta['critical_paths']=sorted(set(map(str, originals+i.runtime_files()+[i.UNITS/(i.NAME+'.service'),i.UNITS/(i.NAME+'.timer')])))
        script=(Path(__file__).parents[1]/'releases'/version/'installer.sh').read_text()
        encoded=script.split("<<'DUPLIKA_4_PAYLOAD'\n",1)[1].split('DUPLIKA_4_PAYLOAD',1)[0]
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)),mode='r:gz') as archive:
            files={m.name:archive.extractfile(m).read() for m in archive}
        for path in i.runtime_files():path.write_bytes(files[path.name])
        tree=ast.parse(files['installer.py'])
        activate=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='activate')
        for node in activate.body:
            if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('service','timer'):
                value=node.value;template=ast.literal_eval(value.func.value if isinstance(value,ast.Call) else value)
                (i.UNITS/(i.NAME+'.'+node.targets[0].id)).write_text(template.replace('__PYTHON__',i.sys.executable))
        self.meta['baseline']=identity.snapshot(self.meta['critical_paths'],config)
        (i.BASE/'manifest.json').write_text(json.dumps(self.meta))
        (i.BASE/'complete').write_text(version+'\n')
        self.originals=originals

    # The parent tests exercise observer paths; these cases exercise legacy review directly.
    test_original_payload_validates_and_storage_hashes_are_verified = None
    test_altered_configuration_and_wrong_target_are_rejected = None
    test_upgrade_preserves_transport_storage_and_checkpoint = None
    test_failed_first_cycle_rolls_back_instead_of_accepting = None

    def test_blueprint_exact_match_then_rebaselined_helper_or_config_change_rejected(self):
        self.assertEqual(i.validate_v4_upgrade(TARGET,{}),self.meta)
        path=Path(self.meta['config_path']);cfg=json.loads(path.read_text())
        cfg['sources'][next(iter(cfg['sources']))]['unexpected']=True
        path.write_text(json.dumps(cfg));self.meta['baseline']=identity.snapshot(self.meta['critical_paths'],str(path))
        with self.assertRaisesRegex(RuntimeError,'legacy_config_differs_from_blueprint'):
            legacy.validate(i,self.meta,TARGET)

    def test_inherited_pipeline_and_offsets_are_not_touched(self):
        cursor=i.CLOUD_DATA/'private/checkpoint';cursor.write_text('offset retained')
        before={p:p.read_bytes() for p in self.originals+[cursor]}
        with patch.object(i,'validate_v4_upgrade',return_value=self.meta),patch.object(identity,'status'),patch.object(i,'execute') as execute,patch.object(i,'activate') as activate,patch.object(i,'backup',return_value={}):
            legacy.upgrade(i,TARGET,{})
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertEqual(execute.call_args_list[0].args[0],['systemctl','stop',i.NAME+'.timer',i.NAME+'.service'])
        self.assertEqual(activate.call_args.args[8],self.variant)
        self.assertNotIn('storage_enabled',activate.call_args.kwargs)

    def test_bad_helper_or_added_critical_path_rejected(self):
        path=self.originals[0];path.write_text('unreviewed helper')
        with self.assertRaisesRegex(RuntimeError,'unreviewed_legacy_helper'):legacy.validate(i,self.meta,TARGET)


class MinimalLegacyTests(LegacyTests):
    variant='almalinux-wordpress-minimal-v4'
