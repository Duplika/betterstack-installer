import unittest
from unittest.mock import Mock, patch

import collector


class DedicatedHostTests(unittest.TestCase):
    def test_host_cycle_ignores_available_lve_and_all_account_readers(self):
        profile = {'capabilities': {'lve_status': 'available'}, 'disk_io_observer': True}
        state = {'boot_id': 'test'}
        output = Mock()
        output.logs, output.metrics = [], []
        with patch.object(collector, 'essential_inventory', side_effect=AssertionError), \
             patch.object(collector, 'scheduled_daily_lve', side_effect=AssertionError), \
             patch.object(collector, 'collect_errors', side_effect=AssertionError), \
             patch.object(collector, 'active_diagnostic', side_effect=AssertionError), \
             patch.object(collector, 'collect_disk_io', return_value='ok'), \
             patch.object(collector, 'atomic_json') as write:
            collector.main_dedicated(profile, state, output, 0)
        marker = write.call_args_list[0].args[1]
        self.assertEqual(marker['monitoring_policy'], 'dedicated-host-v1')
        self.assertEqual(marker['components']['lve'], 'disabled')
        self.assertEqual(marker['components']['disk_io'], 'ok')
        self.assertNotIn('accounts', output.log.call_args.kwargs)
