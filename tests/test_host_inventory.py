import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import identity
import test_upgrade_rc15


class ObserverRc50(test_upgrade_rc15.UpgradeTests):
    previous_version = '4.0.0-rc50'


class ObserverRc52(test_upgrade_rc15.UpgradeTests):
    previous_version = '4.0.0-rc52'


class InventoryTests(unittest.TestCase):
    def test_mount_modes(self):
        for filesystems, expected in (([], 'unknown'), (['cgroup'], 'v1'), (['cgroup2'], 'v2'),
                                      (['cgroup2', 'cgroup'], 'hybrid')):
            rows = ['10 1 0:2 / /sys/fs/cgroup rw - '+fs+' none rw' for fs in filesystems]
            self.assertEqual(identity.cgroup_mode(rows), expected)

    def test_cache_daily_retry_reboot_and_cloudlinux_scope(self):
        with tempfile.TemporaryDirectory(dir='/root') as folder, patch.object(identity,'STATE',Path(folder)), \
                patch.object(identity.time,'time',return_value=100000) as clock, \
                patch.object(identity,'module_inventory',return_value={'comparison':'unknown'}) as probe:
            caps = {'os_id':'almalinux'}
            self.assertNotIn('lve', identity.host_inventory(caps, False))
            self.assertFalse(list(Path(folder).iterdir()))
            self.assertNotIn('lve', identity.host_inventory({'os_id':'cloudlinux','edition':'CloudLinux OS Solo'},False))
            probe.assert_not_called()
            caps = {'os_id':'cloudlinux','edition':'CloudLinux OS Shared'}
            first = identity.host_inventory(caps, True)
            clock.return_value += 3600
            self.assertEqual(identity.host_inventory(caps, True), first)
            receipt = Path(folder)/'host-inventory.json'
            record = json.loads(receipt.read_bytes())
            record['boot'] = 'previous'
            receipt.write_text(json.dumps(record))
            self.assertNotEqual(identity.host_inventory(caps, True)['observed_at'],first['observed_at'])
            clock.return_value += 86400
            pending = identity.host_inventory(caps, True, acknowledged='')
            self.assertEqual(probe.call_count, 2)
            identity.host_inventory(caps, True, acknowledged=pending['observed_at'])
            self.assertEqual(probe.call_count, 3)


class LegacyRc51(unittest.TestCase):
    def test_minimal_base_upgrade_with_equivalent_filesystems(self):
        import base_monitoring
        import filesystem_aliases
        import installer
        import legacy_upgrade
        from test_base36 import LegacyBaseTests
        from test_upgrade_rc15 import TARGET, released_files
        fixture = LegacyBaseTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cfg = base_monitoring.cpu_graph(json.loads(fixture.path.read_text()), 'legacy_host_essential', 'duplika_metrics')
        fixture.path.write_text(json.dumps(cfg))
        version = '4.0.0-rc51'
        fixture.meta.update(agent_version=version, installer_version=version)
        fixture.meta['migration_review']['base_monitoring'] = base_monitoring.MARKER
        released = released_files(version)
        for path in installer.runtime_files():
            path.write_bytes(released[path.name])
        fixture.save()
        (installer.BASE/'complete').write_text(version+'\n')
        graph = filesystem_aliases.prepare(cfg, ['/home'], TARGET['host'])
        with patch.object(legacy_upgrade, 'filesystem_plan', return_value=(graph, ['/home'], True)):
            legacy_upgrade.upgrade(installer, TARGET, fixture.caps, deduplicate=True)
        meta = json.loads((installer.BASE/'manifest.json').read_bytes())
        self.assertTrue(meta['migration_review']['live_mount_flags'])
        legacy_upgrade.validate(installer, meta, TARGET)
        self.assertEqual(meta['agent_version'],installer.VERSION)
