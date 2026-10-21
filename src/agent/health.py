"""Hourly maintenance observations; never patch, reboot or change services."""
from pathlib import Path
import tempfile
from capabilities import run, trusted_executable


def observe(runner=run, finder=trusted_executable):
    result = {'reboot_recommended': None, 'reboot_status': 'unknown',
              'reason': 'reboot_probe_unavailable'}
    reboot = finder('needs-restarting')
    if reboot:
        try:
            # DNF needs logs and a metadata lock even with --cacheonly.
            # Isolate all auxiliary state; keep system DNF paths read-only.
            with tempfile.TemporaryDirectory(prefix='duplika-reboot-', dir='/tmp') as folder:
                root = Path(folder)
                for name in ('logs', 'cache', 'state'):
                    (root / name).mkdir(mode=0o700)
                check = runner([reboot, '-r', '--cacheonly',
                    '--setopt=logdir=' + str(root / 'logs'),
                    '--setopt=cachedir=' + str(root / 'cache'),
                    '--setopt=persistdir=' + str(root / 'state'),
                    '--setopt=logfilelevel=0',
                    '--setopt=log_size=64k', '--setopt=log_rotate=1'])
            result['reboot_probe_exit_code'] = check.returncode
            if check.returncode == 0 and check.stdout.strip() and not check.stderr.strip():
                result.update(reboot_recommended=False, reboot_status='not_indicated', reason='needs_restarting_no_hint')
            elif check.returncode == 1 and check.stdout.strip() and not check.stderr.strip():
                # A maintenance recommendation, not an urgency classification.
                result.update(reboot_recommended=True, reboot_status='indicated',
                              reason='updates_since_boot')
            else:
                result.update(reason='reboot_probe_failed')
        except Exception:
            result.update(reason='reboot_probe_failed')
    return result
