import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import storage
import installer


def mount(i, dev, root, path, fs='ext4', opts='rw', source='/dev/sdb1'):
    return '{} 1 {} {} {} {} - {} {} rw'.format(i, dev, root, path, opts, fs, source)


class StorageTests(unittest.TestCase):
    def test_cagefs_alias_cannot_hide_mysql_volume_and_bind_duplicates(self):
        text='\n'.join([mount(1,'8:1','/','/usr/share/cagefs-skeleton/var/lib/mysql'),
                        mount(2,'8:1','/','/var/lib/mysql'),mount(3,'8:1','/','/home/virtfs/test/var/lib/mysql'),
                        mount(4,'259:1','/','/home',source='/dev/nvme0n1p1'),
                        mount(5,'0:8','/','/tmp',fs='tmpfs'),mount(6,'0:9','/','/remote',fs='nfs')])
        rows=storage.volumes(text)
        self.assertEqual([r['mountpoint'] for r in rows],['/home','/var/lib/mysql'])

    def test_host_readonly_options_and_escaped_paths(self):
        rows=storage.volumes(mount(1,'8:1','/','/backup\\040disk',opts='ro'))
        self.assertEqual(rows[0]['mountpoint'],'/backup disk')
        self.assertTrue(rows[0]['readonly'])
        self.assertFalse(storage.volumes(mount(1,'8:1','/','/home'))[0]['readonly'])

    def test_capacity_reserved_blocks_and_no_inodes(self):
        v=types.SimpleNamespace(f_frsize=4096,f_bsize=4096,f_blocks=100,f_bfree=30,f_bavail=20,f_files=0)
        values=storage.volume_values({'mountpoint':'/','readonly':False},lambda _:v)
        self.assertEqual(values['available_bytes'],20*4096)
        self.assertAlmostEqual(values['used_ratio'],70/90)
        self.assertNotIn('inodes_used_ratio',values)

    def test_raid_degraded_inactive_and_resync(self):
        rows=storage.raid_arrays('md0 : active raid1 sda1[0] sdb1[1]\n 100 blocks [2/2] [UU]\n\nmd1 : active raid1 sda2[0]\n 100 blocks [2/1] [U_]\n recovery = 20%\n\nmd2 : inactive sdc1[0]\n 100 blocks\n')
        self.assertEqual(len(rows),3)
        self.assertFalse(rows[0]['degraded']);self.assertTrue(rows[1]['degraded'])
        self.assertTrue(rows[1]['syncing']);self.assertFalse(rows[2]['active'])
        self.assertIsNone(rows[2]['degraded'])

    def test_smart_bitmask_never_zero_fills_unknown_and_drops_private_fields(self):
        data={'smart_status':{'passed':False},'serial_number':'PRIVATE_SERIAL',
              'nvme_smart_health_information_log':{'percentage_used':12,'media_errors':5}}
        status,values=storage.smart_values(data,8)
        self.assertEqual(status,'observed');self.assertEqual(values['healthy'],0)
        self.assertEqual(values['nvme_percentage_used'],12)
        self.assertNotIn('PRIVATE_SERIAL',json.dumps(values))
        self.assertEqual(storage.smart_values(data,2),('unavailable',{}))
        self.assertEqual(storage.smart_values({},0)[0],'unknown')
        self.assertNotIn('healthy',storage.smart_values({},0)[1])

    def test_ata_no_wakeup_no_selftest_and_timeout(self):
        runner=unittest.mock.Mock(return_value=types.SimpleNamespace(stdout=json.dumps({'smart_status':{'passed':True}}),returncode=0))
        with patch.object(storage,'executable',return_value='/usr/sbin/smartctl'):
            status,_=storage.probe_smart({'device':'/dev/sda','kind':'ata'},runner)
        self.assertEqual(status,'observed')
        args=runner.call_args.args[0]
        self.assertEqual(args,['/usr/sbin/smartctl','-j','-d','ata','-n','standby,3','-H','-A','/dev/sda'])
        self.assertEqual(runner.call_args.kwargs['timeout'],5)
        self.assertEqual(storage.probe_smart({'device':'/dev/vda','kind':None},runner),('unsupported_transport',{}))
        self.assertEqual(runner.call_count,1)

    def test_no_json_fallback_and_sleeping_is_not_healthy(self):
        with patch.object(storage,'executable',return_value='/usr/sbin/smartctl'):
            runner=lambda *a,**kw:types.SimpleNamespace(stdout=json.dumps({'power_mode':'STANDBY'}),returncode=3)
            self.assertEqual(storage.probe_smart({'device':'/dev/sda','kind':'ata'},runner),('sleeping',{}))
            runner=lambda *a,**kw:types.SimpleNamespace(stdout='no json',returncode=0)
            self.assertEqual(storage.probe_smart({'device':'/dev/sda','kind':'ata'},runner),('invalid_response',{}))

    def test_stream_names_match_existing_transport_and_bound_rotation(self):
        from config import make_config
        p={'host':'cloud.example.net','server_ip':'203.0.113.2','reader_gid':0}
        with tempfile.TemporaryDirectory(dir='/root') as folder,patch.object(storage,'ROOT',Path(folder)):
            spool=Path(folder)/'spool';spool.mkdir()
            path=spool/'metrics.jsonl.storage';path.write_text('x'*(1024*1024))
            out=storage.Output(p);out.metric('volume_count',2);out.flush()
            self.assertTrue(path.with_name(path.name+'.1').exists())
            self.assertEqual(json.loads(path.read_text())['gauge']['value'],2)
            self.assertEqual(path.stat().st_mode&0o777,0o640)
            patterns=make_config(p,'TEST_SECRET','s1234568.us-west-2a.betterstackdata.com')['sources']['observer_metrics']['include']
            import fnmatch
            self.assertTrue(fnmatch.fnmatch('/var/lib/duplika-betterstack-cloudlinux/spool/'+path.name,patterns[0]))

    def test_no_repeated_logs_except_daily_or_change(self):
        out=storage.Output({'host':'cloud.example.net','server_ip':'203.0.113.2'})
        state={};out.event(state,100,{'volumes':[]});out.event(state,101,{'volumes':[]})
        self.assertEqual(len(out.logs),1)
        out.event(state,86500,{'volumes':[]});self.assertEqual(len(out.logs),2)

    def test_storage_unit_keeps_transport_isolation_and_denies_network(self):
        self.assertIn('ProtectHome=read-only',installer.STORAGE_SERVICE)
        self.assertIn('ProtectSystem=strict',installer.STORAGE_SERVICE)
        self.assertIn('RestrictAddressFamilies=AF_UNIX',installer.STORAGE_SERVICE)
        self.assertIn('CPUQuota=5%',installer.STORAGE_SERVICE)
        self.assertNotIn('PrivateDevices=true',installer.STORAGE_SERVICE)

    def test_enable_rejects_existing_or_symlink_code_before_backup(self):
        with tempfile.TemporaryDirectory(dir='/root') as folder:
            root=Path(folder);cloud=root/'cloud';cloud.mkdir();units=root/'units';units.mkdir()
            (cloud/'storage.py').symlink_to(root/'missing')
            with patch.object(installer,'CLOUD',cloud),patch.object(installer,'UNITS',units),patch.object(installer,'backup') as backup:
                with self.assertRaisesRegex(RuntimeError,'existing_layout'):
                    installer.enable_storage()
                backup.assert_not_called()

    def test_cycles_spread_smart_queries_and_preserve_six_hour_interval(self):
        with tempfile.TemporaryDirectory(dir='/root') as folder:
            root=Path(folder);base=root/'base';base.mkdir();data=root/'data';data.mkdir()
            (data/'private').mkdir();(data/'spool').mkdir()
            (base/'profile.json').write_text(json.dumps({'host':'cloud.example.net','server_ip':'203.0.113.2','reader_gid':0}))
            from contextlib import ExitStack
            with ExitStack() as stack:
                stack.enter_context(patch.object(storage,'ROOT',data));stack.enter_context(patch.object(storage,'BASE',base))
                stack.enter_context(patch.object(storage,'volumes',return_value=[{'device':'/dev/sda1','mountpoint':'/','fstype':'ext4','readonly':False}]))
                stack.enter_context(patch.object(storage,'volume_values',return_value={'total_bytes':1000,'available_bytes':500}))
                stack.enter_context(patch.object(storage,'raid_arrays',return_value=[]))
                stack.enter_context(patch.object(storage,'devices',return_value=[{'device':'/dev/sda','kind':'ata'},{'device':'/dev/sdb','kind':'ata'}]))
                probe=stack.enter_context(patch.object(storage,'probe_smart',return_value=('observed',{'healthy':1})))
                storage.main();self.assertEqual(probe.call_count,1)
                storage.main();self.assertEqual(probe.call_count,2)
                storage.main();self.assertEqual(probe.call_count,2)
                state=json.loads((data/'private/storage-state.json').read_text())
                self.assertEqual(len(state['smart']),2)
                self.assertEqual(json.loads((data/'private/storage-health.json').read_text())['probe_failures'],0)
