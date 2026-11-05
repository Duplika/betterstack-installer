"""Migrate exact released legacy bases; reject drift and roll back failures."""
import base64
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import tarfile
import unittest
from unittest.mock import patch

import identity
import installer as i
import legacy_upgrade
import legacy_essential
import minimal_host as m
import test_release18 as previous
from test_upgrade_rc15 import TARGET
from test_legacy_essential30 import rows


class MinimalHostTests(previous.LegacyTests):
    release = '4.0.0-rc34'
    test_blueprint_exact_match_then_rebaselined_helper_or_config_change_rejected = None
    test_inherited_pipeline_and_offsets_are_not_touched = None
    test_bad_helper_or_added_critical_path_rejected = None

    def setUp(self):
        super().setUp()
        script = (Path(__file__).parents[1]/'releases'/self.release/'installer.sh').read_text()
        encoded = script.split("<<'DUPLIKA_4_PAYLOAD'\n", 1)[1].split('DUPLIKA_4_PAYLOAD', 1)[0]
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)), mode='r:gz') as archive:
            files = {x.name: archive.extractfile(x).read() for x in archive}
        for path in i.runtime_files():
            path.write_bytes(files[path.name])
        proof = json.loads((i.HERE/('upgrade_'+self.release.split('-')[1]+'.json')).read_text())
        (i.UNITS/(i.NAME+'.service')).write_text(proof['identity_service_template'].replace('__PYTHON__',i.sys.executable))
        self.caps = {'os_id':'almalinux','os_version':'9.8'}
        self.meta.update(agent_version=self.release,installer_version=self.release,capabilities=self.caps)
        self.path = Path(self.meta['config_path']); self.path.chmod(0o640)
        self.before_config = legacy_essential.prepare(json.loads(self.path.read_text()))
        self.path.write_text(json.dumps(self.before_config))
        self.meta['migration_review'] = {'essential_host_sampling':legacy_essential.MARKER}
        self.save()
        self.active = set(self.meta['services']); self.enabled = set(self.meta['services'])
        self.calls = []
        self.stack.enter_context(patch.object(identity,'status',return_value={}))
        self.stack.enter_context(patch.object(i.context_cleanup,'validate'))
        self.stack.enter_context(patch.object(legacy_essential,'native_probe',return_value=legacy_essential.coverage(rows())))
        self.stack.enter_context(patch.object(i,'trusted_executable',side_effect=lambda name:
            i.sys.executable if name == i.sys.executable else str(self.vector) if name == 'vector' else None))
        self.stack.enter_context(patch.object(i,'run',side_effect=self.system))
        self.stack.enter_context(patch.object(i,'execute',side_effect=self.system))

    def save(self):
        self.meta['baseline'] = identity.snapshot(self.meta['critical_paths'],self.meta['config_path'])
        (i.BASE/'manifest.json').write_text(json.dumps(self.meta))
        (i.BASE/'complete').write_text(self.meta['agent_version']+'\n')

    def system(self,args,**kw):
        self.calls.append(args)
        code = 0; output = ''
        if args[:2] == ['systemctl','is-active']:
            code = 0 if args[-1] in self.active else 3
        elif args[:2] == ['systemctl','is-enabled']:
            code = 0 if args[-1] in self.enabled else 1
            output = 'enabled' if code == 0 else 'disabled'
        elif args[:2] == ['systemctl','show']:
            output = 'loaded'
        elif args[:2] == ['systemctl','stop']:
            self.active.difference_update(args[2:])
        elif args[:2] == ['systemctl','start']:
            self.active.update(args[2:])
        elif args[:2] == ['systemctl','disable']:
            units = [a for a in args[2:] if not a.startswith('--')]
            self.enabled.difference_update(units)
            if '--now' in args:self.active.difference_update(units)
        elif args[:2] == ['systemctl','enable']:
            units = [a for a in args[2:] if not a.startswith('--')]
            self.enabled.update(units)
            if '--now' in args:self.active.update(units)
        return subprocess.CompletedProcess(args,code,output,'')

    def test_preflight_no_writes_or_services_changed(self):
        before = self.path.read_bytes(); active = set(self.active)
        with patch.object(i,'backup') as backup:
            old, proposed, proof = m.plan(i,TARGET,self.caps)
        backup.assert_not_called()
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.active,active)
        self.assertEqual(set(proposed['sources']),{'duplika_host','duplika_host_slow'})
        self.assertEqual(proof['application_readers'],0)

    def test_success_removes_readers_keeps_metrics_credentials_data_and_is_repeatable(self):
        cursor = self.root/'checkpoint'; cursor.write_text('offset advanced')
        preserved = {p:p.read_bytes() for p in self.originals if p != self.path}
        before = copy.deepcopy(self.before_config)
        m.migrate(i,TARGET,self.caps,apply=True)
        cfg = json.loads(self.path.read_text())
        self.assertEqual(cfg,m.prepare(before))
        self.assertEqual(cfg['data_dir'],before['data_dir'])
        self.assertEqual(cfg['sinks']['duplika_metrics'],before['sinks']['duplika_metrics'])
        self.assertEqual(set(cfg['sinks']),{'duplika_metrics'})
        self.assertEqual(len(cfg['sources']),2)
        self.assertFalse(any(x['type'] in ('file','journald') for x in cfg['sources'].values()))
        self.assertEqual(cursor.read_text(),'offset advanced')
        for path,data in preserved.items():self.assertEqual(path.read_bytes(),data)
        self.assertEqual(self.path.stat().st_mode & 0o777,0o640)
        meta = json.loads((i.BASE/'manifest.json').read_text())
        self.assertEqual(meta['services'],[self.meta['services'][0],i.NAME+'.timer'])
        self.assertEqual(meta['oneshots'],[])
        self.assertEqual(meta['resource_sampling']['wordpress_errors'],'disabled')
        timer,service = m.retired_units(meta)
        self.assertNotIn(timer,self.active);self.assertNotIn(timer,self.enabled)
        self.assertNotIn(service,self.active)
        legacy_upgrade.validate(i,meta,TARGET)
        identity.destination(cfg,TARGET,allow_metrics_only=True)
        with self.assertRaisesRegex(RuntimeError,'ambiguous_destination'):
            identity.destination(cfg,TARGET)
        with patch.object(i,'backup') as backup,contextlib.redirect_stdout(io.StringIO()):
            m.migrate(i,TARGET,self.caps,apply=True)
        backup.assert_not_called()

    def test_changed_blueprint_even_with_new_baseline_rejected(self):
        cfg=json.loads(self.path.read_text());cfg['sources']['duplika_wordpress']['include']=['/unexpected']
        self.path.write_text(json.dumps(cfg));self.save()
        with patch.object(i,'backup') as backup:
            with self.assertRaisesRegex(RuntimeError,'differs_from_blueprint'):
                m.migrate(i,TARGET,self.caps,apply=True)
        backup.assert_not_called()

    def test_second_transport_or_unknown_state_rejected(self):
        other = 'vector.service' if self.meta['services'][0] != 'vector.service' else 'duplika-betterstack.service'
        self.active.add(other)
        with self.assertRaisesRegex(RuntimeError,'second_transport_active'):m.plan(i,TARGET,self.caps)
        self.active.remove(other)
        with patch.object(i,'run',return_value=subprocess.CompletedProcess([],1,'','')):
            with self.assertRaisesRegex(RuntimeError,'transport_state_unavailable'):m.plan(i,TARGET,self.caps)

    def test_failure_restores_files_and_timer_enablement_without_rewinding_cursor(self):
        paths = [self.path,*i.runtime_files(),i.BASE/'manifest.json',i.BASE/'complete']
        original = {p:p.read_bytes() for p in paths}
        active,enabled = set(self.active),set(self.enabled)
        cursor = self.root/'checkpoint';cursor.write_text('old')
        def fail(args,**kw):
            if args[:3] == ['runuser','-u','vector']:
                cursor.write_text('advanced')
                raise RuntimeError('native_validation_failed')
            return self.system(args,**kw)
        with patch.object(i,'execute',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'native_validation_failed'):
                m.migrate(i,TARGET,self.caps,apply=True)
        for path,data in original.items():self.assertEqual(path.read_bytes(),data)
        self.assertEqual(self.active,active);self.assertEqual(self.enabled,enabled)
        self.assertEqual(cursor.read_text(),'advanced')

    def test_metrics_only_destination_never_accepts_another_origin_or_log_reader(self):
        cfg=m.prepare(self.before_config)
        bad=copy.deepcopy(cfg);bad['sinks']['duplika_metrics']['uri']='https://example.net/metrics'
        with self.assertRaisesRegex(RuntimeError,'unexpected_destination'):
            identity.destination(bad,TARGET,allow_metrics_only=True)
        bad=copy.deepcopy(cfg);bad['sources']['duplika_host']['type']='file'
        with self.assertRaisesRegex(RuntimeError,'unexpected_minimal_graph'):
            identity.destination(bad,TARGET,allow_metrics_only=True)

    def test_retired_timer_reactivation_fails_repeat_verification(self):
        m.migrate(i,TARGET,self.caps,apply=True)
        meta=json.loads((i.BASE/'manifest.json').read_text())
        self.enabled.add(m.retired_units(meta)[0])
        with self.assertRaisesRegex(RuntimeError,'acl_timer_not_disabled'):
            m.migrate(i,TARGET,self.caps)


class MinimalContextTests(MinimalHostTests):
    variant='almalinux-wordpress-minimal-v4'
    release='4.0.0-rc30'
