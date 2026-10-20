"""Hourly maintenance observations; never patch, reboot or change services."""
from pathlib import Path
import tempfile
from capabilities import run, trusted_executable


def observe(runner=run, finder=trusted_executable):
    result = {'kernelcare': 'not_installed', 'reboot_status': 'unknown',
              'priority': 'unknown', 'reason': 'reboot_probe_unavailable',
              'urgency_verified': False}
    kcare = finder('kcarectl')
    if kcare:
        try:
            status = runner([kcare, '--status'])
            result['kernelcare'] = {0: 'patched_current_reported', 1: 'no_patches_applied',
                2: 'patches_pending', 3: 'kernel_unsupported'}.get(status.returncode, 'unknown')
            result['kernelcare_status_code'] = status.returncode
            # Store no descriptions, host identifiers or license information.
            effective = runner([kcare, '--uname'])
            value = effective.stdout.strip()
            if effective.returncode == 0 and len(value) <= 128:
                import re
                if re.fullmatch(r'[A-Za-z0-9_.+-]+', value):
                    result['effective_kernel'] = value
        except Exception:
            result['kernelcare'] = 'probe_failed'
    reboot = finder('needs-restarting')
    if reboot:
        try:
            # DNF opens auxiliary logs even for this read-only query. Keep them
            # private and ephemeral; never grant the service access to /var/log.
            with tempfile.TemporaryDirectory(prefix='duplika-reboot-', dir='/tmp') as logdir:
                check = runner([reboot, '-r', '--cacheonly',
                    '--setopt=logdir=' + logdir, '--setopt=logfilelevel=0',
                    '--setopt=log_size=64k', '--setopt=log_rotate=1'])
            result['reboot_probe_exit_code'] = check.returncode
            if check.returncode == 0:
                result.update(reboot_status='not_indicated', priority='none', reason='needs_restarting_no_hint')
            elif check.returncode == 1 and check.stdout.strip():
                # This hint does not reliably separate kernel from core libraries.
                # Even with KernelCare, do not label a generic hint safe to defer.
                result.update(reboot_status='indicated', priority='review',
                              reason='reboot_hint_requires_component_review')
            else:
                result.update(reason='reboot_probe_failed')
        except Exception:
            result.update(reason='reboot_probe_failed')
    if result['kernelcare'] in ('patches_pending', 'kernel_unsupported', 'no_patches_applied'):
        result.update(priority='attention', patch_action='review_kernel_protection')
    # No CVE/exposure evidence here: absent KernelCare is not an urgent reboot.
    return result
