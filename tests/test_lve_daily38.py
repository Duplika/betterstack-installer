"""Daily LVE report uses the existing observer and a closed day."""
import datetime as dt
import json
import unittest
from unittest.mock import Mock, patch

import collector
import identity
import installer
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

    def test_filtered_closed_day_and_no_duplicate_local_cycle(self):
        included = dict(self.row, username='over', faults={'cpu': {'lve': 9}, 'pmem': {'lve': 12}})
        boundary = dict(self.row, username='boundary', faults={'cpu': {'lve': 8}, 'pmem': {'lve': 12}})
        data = json.dumps({'result': 'success', 'users': [self.row, included, boundary, {'username': 'N/A'}]})
        with patch.object(collector, 'statistics_executable', return_value='/usr/sbin/cloudlinux-statistics'), \
                patch.object(collector, 'command', return_value=data) as run:
            self.assertEqual(collector.collect_daily_lve(self.output, self.state, self.profile, self.now), 'available')
            self.assertEqual(collector.collect_daily_lve(self.output, self.state, self.profile, self.now + 60), 'available')
        run.assert_called_once_with(['/usr/sbin/cloudlinux-statistics', '--json', '--from', '2026-11-13 03:00',
                                     '--to', '2026-11-14 03:00', '--limit=0', '--show', 'all'], timeout=45, utc=True)
        account, complete = self.output.log.call_args_list
        self.assertEqual(account.args, ('account_resource_daily',))
        self.assertEqual(account.kwargs['resources']['cpu'], {'faults': 9, 'usage_average': 4.5})
        self.assertEqual(complete.args, ('account_resource_daily_complete',))
        self.assertEqual(complete.kwargs['account_count'], 1)
        self.assertEqual(complete.kwargs['evaluated_account_count'], 3)
        self.assertEqual(complete.kwargs['fault_threshold'], 20)
        self.assertEqual(complete.kwargs['report_id'], account.kwargs['report_id'])
        self.assertEqual(complete.kwargs['skipped_placeholders'], 1)

    def test_complete_report_with_no_published_accounts(self):
        with patch.object(collector, 'statistics_executable'), patch.object(
                collector, 'command', return_value=json.dumps({'result': 'success', 'users': [self.row]})):
            self.assertEqual(collector.collect_daily_lve(self.output, self.state, self.profile, self.now), 'available')
        self.output.log.assert_called_once()
        complete = self.output.log.call_args
        self.assertEqual(complete.args, ('account_resource_daily_complete',))
        self.assertEqual(complete.kwargs['account_count'], 0)
        self.assertEqual(complete.kwargs['evaluated_account_count'], 1)

    def test_busy_cycles_keep_previous_status_without_requerying(self):
        with patch.object(collector, 'collect_daily_lve', return_value='available') as daily:
            self.assertIsNone(collector.scheduled_daily_lve(self.output, self.state, self.profile, self.now, 8))
            self.assertEqual(collector.scheduled_daily_lve(self.output, self.state, self.profile, self.now, 1), 'available')
            self.state['pressure_v27'] = {'attempt_at': self.now + 60}
            self.assertEqual(collector.scheduled_daily_lve(self.output, self.state, self.profile, self.now + 60, 1), 'available')
            self.assertEqual(collector.scheduled_daily_lve(self.output, self.state, self.profile, self.now + 120, 8), 'available')
        daily.assert_called_once()

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


class ReviewedRc38Tests(test_upgrade_rc15.UpgradeTests):
    previous_version = '4.0.0-rc38'

    def test_only_reviewed_pilot_is_an_allowed_alternate(self):
        collector_path = installer.CLOUD / 'collector.py'
        pilot = collector_path.read_text().replace(
            "'--show=all']\n        rows, skipped = daily_lve_rows(json.loads(command(args, timeout=18, utc=True)))",
            "'--show', 'all']\n        rows, skipped = daily_lve_rows(json.loads(command(args, timeout=45, utc=True)))")
        pilot = pilot.replace(
            """        for row in rows:
            out.log('account_resource_daily', **report, **row)
        out.log('account_resource_daily_complete', **report, account_count=len(rows), skipped_placeholders=skipped)""",
            """        review_threshold = 20
        published = 0
        for row in rows:
            faults = sum(resource['faults'] for resource in row['resources'].values())
            if faults > review_threshold:
                out.log('account_resource_daily', **report, **row)
                published += 1
        out.log('account_resource_daily_complete', **report, account_count=published,
                evaluated_account_count=len(rows), fault_threshold=review_threshold,
                skipped_placeholders=skipped)""")
        collector_path.write_text(pilot)
        with self.assertRaisesRegex(RuntimeError, 'upgrade:v4_drift'):
            installer.validate_v4_upgrade(test_upgrade_rc15.TARGET, {})
        self.meta['baseline'] = identity.snapshot(self.meta['critical_paths'], self.meta['config_path'])
        (installer.BASE / 'manifest.json').write_text(json.dumps(self.meta))
        self.assertEqual(installer.validate_v4_upgrade(test_upgrade_rc15.TARGET, {})['agent_version'],
                         self.previous_version)
        collector_path.write_text(pilot + '\n# unreviewed change\n')
        self.meta['baseline'] = identity.snapshot(self.meta['critical_paths'], self.meta['config_path'])
        (installer.BASE / 'manifest.json').write_text(json.dumps(self.meta))
        with self.assertRaisesRegex(RuntimeError, 'upgrade:unreviewed_v4_runtime'):
            installer.validate_v4_upgrade(test_upgrade_rc15.TARGET, {})
