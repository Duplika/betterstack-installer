import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/agent'))
import collector
import installer


class ObserverUpgradeTests(unittest.TestCase):
    def test_reviewed_cron_corrected_once(self):
        with tempfile.TemporaryDirectory() as directory:
            cron = Path(directory) / 'cron'
            script = Path(directory) / 'daily'
            script.write_bytes(b'reviewed contents')
            expected_digest = hashlib.sha256(script.read_bytes()).hexdigest()
            original = (b'CRON_TZ=America/Argentina/Buenos_Aires\n'
                        b'23,38,53 0 * * * root /usr/bin/systemd-run --quiet --wait --pipe --collect '
                        b'-p CPUQuota=10% -p Nice=15 -p IOSchedulingClass=idle -p MemoryMax=256M '
                        b'-p NoNewPrivileges=yes -p RuntimeMaxSec=135 /usr/bin/python3 '
                        b'/usr/local/sbin/duplika-lve-daily observer.example.com\n')
            corrected = original.replace(b'10%', br'10\%')
            quiet = corrected.removesuffix(b'\n') + b' >/dev/null\n'
            with patch.multiple(installer, DAILY_LVE_CRON=cron, DAILY_LVE_SCRIPT=script,
                                DAILY_LVE_SCRIPT_DIGESTS={expected_digest}), patch.object(installer.identity, 'read', Path.read_bytes):
                host = Mock(wraps='observer.example.com')
                host.endswith.return_value = True
                target = {'host': host}
                caps = {'os_id': 'cloudlinux'}
                self.assertIsNone(installer.external_lve_cron(target, caps))
                cron.write_bytes(original)
                self.assertEqual(installer.external_lve_cron(target, caps), (original, quiet))
                cron.write_bytes(corrected)
                self.assertEqual(installer.external_lve_cron(target, caps), (corrected, quiet))
                cron.write_bytes(quiet)
                self.assertEqual(installer.external_lve_cron(target, caps), (quiet, quiet))
                cron.write_bytes(quiet.replace(b'>/dev/null', b'2>/dev/null'))
                with self.assertRaisesRegex(RuntimeError, 'unreviewed_lve_daily_cron'):
                    installer.external_lve_cron(target, caps)
                cron.write_bytes(quiet)
                script.write_bytes(b'unexpected contents')
                with self.assertRaisesRegex(RuntimeError, 'unreviewed_lve_daily_script'):
                    installer.external_lve_cron(target, caps)
                host.endswith.assert_called_with('.duplika.com')

    def test_kernel_cursor_and_oom_counts_survive_batch_boundary(self):
        class Output:
            def __init__(self):
                self.events = []

            def log(self, _, **event):
                self.events.append(event)

        def line(cursor, ts, message):
            return json.dumps({'__CURSOR': cursor, '__REALTIME_TIMESTAMP': str(ts * 1000000),
                               'MESSAGE': message}).encode()

        out = Output()
        state = {}
        chunks = [
            ([line('A', 599, 'oom-kill:constraint=CONSTRAINT_MEMCG,oom_memcg=/lve.slice/lve1000000018/lve19,task=lsphp,pid=25,uid=19')], True),
            ([line('B', 601, 'Memory cgroup out of memory: Killed process 25 (lsphp)')], False),
        ]
        accounts, users = {'example'}, {18: 'partner', 19: 'example'}
        with patch.object(collector, 'read_kernel_batch', side_effect=chunks) as read,                 patch.object(collector.time, 'time', return_value=1000):
            collector.collect_kernel(out, state, accounts, users)
            self.assertEqual(out.events, [])
            self.assertEqual(state['journal_cursor'], 'A')
            collector.collect_kernel(out, state, accounts, users)
        self.assertEqual(read.call_args.args[0][-2:], ['--after-cursor', 'A'])
        self.assertEqual(state['journal_cursor'], 'B')
        self.assertEqual(len(out.events), 1)
        self.assertEqual(out.events[0]['oom_events'], 1)
        self.assertEqual(out.events[0]['killed_processes'], 1)
        self.assertEqual(out.events[0]['lve_id'], 19)
        self.assertEqual(out.events[0]['identity_scope'], 'account')
        self.assertEqual(out.events[0]['user'], 'example')

    def test_reseller_oom_requires_cpanel_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / 'resellers'
            registry.write_text('partner: all\n')
            accounts = {'example'}
            users = {18: 'partner', 19: 'example', 20: 'daemon'}
            with patch.object(collector, 'CPANEL_RESELLERS', registry):
                self.assertEqual(collector.oom_identity(1000000018, accounts, users),
                                 {'identity_scope': 'reseller', 'reseller': 'partner'})
                for lve_id in (18, 20, 99, 1000000019):
                    self.assertEqual(collector.oom_identity(lve_id, accounts, users),
                                     {'identity_scope': 'unknown'})

    def test_oldest_entries_are_returned_when_batch_fills(self):
        count = collector.KERNEL_JOURNAL_BATCH
        rows, backlog = collector.read_kernel_batch(
            [sys.executable, '-c', f'for i in range({count + 100}): print(i)'])
        self.assertTrue(backlog)
        self.assertEqual(rows, [str(index).encode() for index in range(count)])
