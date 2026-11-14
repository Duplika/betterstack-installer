"""Daily LVE report uses the existing observer and a closed day."""
import datetime as dt
import unittest
from unittest.mock import Mock, patch

import collector
import test_upgrade_rc15


class DailyLveTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 14, 3, 21, tzinfo=dt.timezone.utc).timestamp()
        self.output = Mock()
        self.state = {}
        self.profile = {'capabilities': {'lve_status': 'available'}}
        self.row = {'username': 'example', 'domain': 'example.test', 'reseller': 'root',
                    'faults': {'cpu': {'lve': 0}, 'pmem': {'lve': 12}},
                    'usage': {'cpu': {'lve': 4.5}, 'pmem': {'lve': 1048576}}}

    def test_unfiltered_closed_day_and_no_duplicate_local_cycle(self):
        data = collector.json.dumps({'result': 'success', 'users': [self.row, {'username': 'N/A'}]})
        with patch.object(collector, 'statistics_executable', return_value='/usr/sbin/cloudlinux-statistics'), \
                patch.object(collector, 'command', return_value=data) as run:
            self.assertEqual(collector.collect_daily_lve(self.output, self.state, self.profile, self.now), 'available')
            self.assertEqual(collector.collect_daily_lve(self.output, self.state, self.profile, self.now + 60), 'available')
        run.assert_called_once_with(['/usr/sbin/cloudlinux-statistics', '--json', '--from', '2026-11-13 03:00',
                                     '--to', '2026-11-14 03:00', '--limit=0', '--show=all'], timeout=18, utc=True)
        account, complete = self.output.log.call_args_list
        self.assertEqual(account.args, ('account_resource_daily',))
        self.assertEqual(account.kwargs['resources']['cpu'], {'faults': 0, 'usage_average': 4.5})
        self.assertEqual(complete.args, ('account_resource_daily_complete',))
        self.assertEqual(complete.kwargs['account_count'], 1)
        self.assertEqual(complete.kwargs['skipped_placeholders'], 1)

    def test_invalid_or_duplicate_source_cannot_emit_complete_report(self):
        data = collector.json.dumps({'result': 'success', 'users': [self.row, self.row]})
        with patch.object(collector, 'statistics_executable', return_value='/usr/sbin/cloudlinux-statistics'), \
                patch.object(collector, 'command', return_value=data) as run:
            for now in (self.now, self.now + 60):
                self.assertEqual(collector.collect_daily_lve(self.output, self.state, self.profile, now), 'retry_pending')
        run.assert_called_once()
        self.output.log.assert_called_once()
        self.assertEqual(self.output.log.call_args.args, ('collector_error',))
        self.assertNotIn('day', self.state['daily_lve_v38'])


class ReviewedRc37Tests(test_upgrade_rc15.UpgradeTests):
    previous_version = '4.0.0-rc37'
