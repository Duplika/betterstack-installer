"""Read-only EA4 INI links and existing reboot hint telemetry."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import health as h
import identity
import test_distribution as distribution


class IniTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/root');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)/'root'
        (self.base/'etc/php.d').mkdir(parents=True)
        self.real=self.base/'etc/real.ini';self.real.write_text('; Configuración\nextension=timezonedb.so\n')
        self.link=self.base/'etc/php.ini';self.link.symlink_to(self.real)

    def test_root_owned_same_runtime_link_and_unicode_comments(self):
        value=h.ea_ini_text(self.link,self.base)
        self.assertIn('Configuración',value)
        self.assertIn('extension=timezonedb.so',value)

    def test_link_outside_runtime_is_rejected_without_read(self):
        self.link.unlink();outside=self.base.parent/'outside.ini';outside.write_text('private')
        self.link.symlink_to(outside)
        with self.assertRaises(ValueError):h.ea_ini_text(self.link,self.base)

    def test_writable_ini_is_rejected(self):
        self.real.chmod(0o666)
        with self.assertRaises(ValueError):h.ea_ini_text(self.link,self.base)


class RebootMetricTests(unittest.TestCase):
    def test_recommendation_unknown_and_no_reboot_are_distinct(self):
        target={'host':'cloud.example.net','server_ip':'203.0.113.20'}
        for state,expected in [('indicated',1),('not_indicated',0),('unknown',-1)]:
            row=h.reboot_metrics({'reboot_status':state},target,1000)[0]
            self.assertEqual(row['gauge']['value'],expected)
            self.assertEqual(row['dt'],'1970-01-01T00:16:40+00:00')
            self.assertEqual(row['name'],'duplika_reboot_recommended')


class RebootDeliveryTests(distribution.IdentityDeliveryTests):
    def test_reboot_metrics_send_on_identity_change_not_every_hour(self):
        real=h.reboot_metrics
        # The inherited fixture isolates metric sends. Supply production shape.
        def metric(observation,target,now):
            return [{'dt':str(now),'name':'duplika_reboot_recommended','gauge':{'value':0},'tags':{'host':target['host']}}]
        with patch.object(h,'reboot_metrics',side_effect=metric):
            identity.status(publish=True)
            self.assertEqual(self.send.call_count,2)
            self.assertTrue(self.send.call_args_list[0].args[0].endswith('/metrics'))
            self.clock.return_value+=3600
            identity.status(publish=True)
            self.assertEqual(self.send.call_count,2)
            self.probe.return_value={'reboot_status':'indicated'}
            identity.status(publish=True)
            self.assertEqual(self.send.call_count,4)
