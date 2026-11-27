"""Hourly integrity/maintenance identity. No application hashes or raw logs."""
import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
from capabilities import VERSION, SCHEMA, mount_flags_source, SYSTEM_MOUNTS
import health

BASE = Path('/etc/duplika-betterstack-v4')
STATE = Path('/var/lib/duplika-betterstack-v4')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def read(path):
    path = Path(path)
    if path.resolve() != path or path.is_symlink():
        raise RuntimeError('redirected_path')
    for p in (path, *path.parents):
        s = p.stat()
        if s.st_uid != 0 or s.st_mode & 0o022:
            raise RuntimeError('unsafe_path')
    if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise RuntimeError('unexpected_file_size')
    return path.read_bytes()


def destination(config, target, allow_metrics_only=False):
    tokens = set()
    paths = set()
    origin = 'https://' + target['ingesting_host']
    for sink in config.get('sinks', {}).values():
        if sink.get('uri') not in (origin + '/', origin + '/metrics') or sink.get('auth', {}).get('strategy') != 'bearer':
            raise RuntimeError('unexpected_destination')
        paths.add(sink['uri'])
        tokens.add(sink['auth']['token'])
    expected = {origin + '/', origin + '/metrics'}
    if allow_metrics_only:
        sources = dict(config.get('sources', {}))
        flags = sources.pop('mount_flags', None)
        if (set(sources) != {'duplika_host', 'duplika_host_slow'}
                or any(x.get('type') != 'host_metrics' for x in sources.values())
                or (flags is not None and flags != mount_flags_source())
                or set(config.get('sinks', {})) != {'duplika_metrics'}):
            raise RuntimeError('unexpected_minimal_graph')
        expected = {origin + '/metrics'}
    if paths != expected or len(tokens) != 1:
        raise RuntimeError('ambiguous_destination')
    token = next(iter(tokens))
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', token):
        raise RuntimeError('invalid_credential')
    return origin + '/', token


def snapshot(paths, config_path):
    hashes = {}
    for path in sorted(set(paths)):
        data = read(path)
        if path == config_path:
            config = json.loads(data)
            config.pop('tests', None)
            for sink in config.get('sinks', {}).values():
                if 'auth' in sink:
                    sink['auth']['token'] = '<SOURCE_TOKEN>'
            data = canonical(config)
        hashes[path] = hashlib.sha256(data).hexdigest()
    return hashes


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(uri, token, event):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(uri, data=canonical(event), method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    try:
        with opener.open(request, timeout=15) as response:
            if response.status not in (200, 202, 204):
                raise RuntimeError('ingestion_rejected')
    except Exception:
        raise RuntimeError('identity_delivery_unconfirmed') from None


def atomic(path, obj):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(canonical(obj)); f.flush(); os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def cgroup_mode(lines):
    mounted = set()
    for line in lines:
        left, sep, right = line.partition(' - ')
        fields = left.split()
        if sep and len(fields) > 4 and right.split():
            mounted.add(right.split()[0])
    modes = mounted & {'cgroup', 'cgroup2'}
    if len(modes) == 2:
        return 'hybrid'
    if 'cgroup2' in modes:
        return 'v2'
    return 'v1' if 'cgroup' in modes else 'unknown'


def module_inventory(kernel):
    from capabilities import trusted_executable, ProbeError
    modules = Path('/sys/module')
    names = ('kmodlve', 'lve')
    loaded = next((name for name in names if (modules / name).is_dir()), None)
    value = {'loaded': bool(loaded), 'loaded_version': None, 'disk_version': None,
             'comparison': 'unknown'}
    def version(raw):
        raw = raw.strip()
        return raw if re.fullmatch(r'[a-zA-Z0-9_.+-]{1,128}', raw) else None
    if loaded:
        try:
            value['loaded_version'] = version((modules / loaded / 'version').read_text())
        except OSError:
            pass
    attempts = []
    value['disk_probe'] = {'attempts': attempts, 'reason': 'command_missing'}
    try:
        command = trusted_executable('modinfo')
    except (OSError, ProbeError):
        command = None
        value['disk_probe']['reason'] = 'command_unavailable'
    if command:
        for name in dict.fromkeys(filter(None, (loaded, *names))):
            attempt = {'module': name, 'exit_code': None}
            attempts.append(attempt)
            try:
                result = health.run([command, '-k', kernel, '-F', 'version', name], timeout=4)
                attempt['exit_code'] = result.returncode
                if result.returncode:
                    reason = 'module_not_found' if 'not found' in result.stderr.lower() else 'command_failed'
                else:
                    value['disk_version'] = version(result.stdout)
                    reason = 'ok' if value['disk_version'] else 'missing_or_invalid_version'
            except subprocess.TimeoutExpired:
                reason = 'timeout'
            except ProbeError as error:
                reason = {'timeout': 'timeout', 'output_limit': 'output_limit'}.get(
                    str(error).partition(':')[0], 'probe_failed')
            except (OSError, ValueError):
                reason = 'probe_failed'
            attempt['reason'] = value['disk_probe']['reason'] = reason
            if reason == 'ok':
                break
    if value['loaded_version'] and value['disk_version']:
        value['comparison'] = 'match' if value['loaded_version'] == value['disk_version'] else 'mismatch'
    return value


def host_inventory(caps, persist, acknowledged=None):
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    now = time.time()
    receipt = STATE / 'host-inventory.json'
    try:
        cached = json.loads(read(receipt))
    except FileNotFoundError:
        cached = {}
    if cached.get('boot') == boot:
        pending = acknowledged is not None and cached['sample']['observed_at'] != acknowledged
        if pending or 0 <= now - cached['at'] < 86400:
            return cached['sample']
    sample = {'observed_at': datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat(),
              'kernel': os.uname().release, 'cgroups': 'unknown'}
    try:
        with Path('/proc/self/mountinfo').open() as mounts:
            sample['cgroups'] = cgroup_mode(mounts)
    except OSError:
        pass
    if caps.get('os_id') == 'cloudlinux' and caps.get('edition') in (
            'CloudLinux OS Shared', 'CloudLinux OS Shared Pro', 'CloudLinux OS Admin'):
        sample['lve'] = module_inventory(sample['kernel'])
    if persist:
        atomic(receipt, {'boot': boot, 'at': now, 'sample': sample})
    return sample


def cgroups_sample(meta, now):
    result = health.run(['/usr/bin/stat', '-fc', '%T', '/sys/fs/cgroup/'], timeout=5)
    root_fs = result.stdout.strip()
    if result.returncode or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,32}', root_fs):
        raise RuntimeError('cgroups_stat_failed')

    def label(path):
        try:
            with path.open() as stream:
                value = stream.read(129).strip()
        except (OSError, ValueError):
            return 'sin_dato'
        return value if re.fullmatch(r'[a-zA-Z0-9_.+-]{1,128}', value) else 'sin_dato'

    modules = Path('/sys/module')
    module = next((name for name in ('kmodlve', 'lve') if (modules / name).is_dir()), None)
    mode = {'cgroup2fs': 'v2', 'tmpfs': 'no_unificado', 'cgroupfs': 'no_unificado'}
    return {'dt': now.isoformat(), 'name': 'duplika_cloudlinux_cgroups_info',
        'gauge': {'value': 1}, 'tags': {'host': meta['target']['host'],
        'mode': mode.get(root_fs, 'desconocido'), 'root_fs': root_fs,
        'lve_loaded': 'yes' if module else 'no', 'lve_module': module or 'sin_modulo',
        'lve_version': label(modules / module / 'version') if module else 'sin_dato',
        'kernel': os.uname().release, 'os_version': meta['capabilities']['os_version'],
        'tuned_profile': label(Path('/etc/tuned/active_profile'))}}


def cgroups_inventory(meta, uri, token, publish, now):
    """Capture once per UTC calendar month; retry the same timestamp on delivery failure."""
    receipt = STATE / 'cgroups-inventory.json'
    period = now.strftime('%Y-%m')
    status = {'cadence': 'monthly', 'period_utc': period, 'status': 'not_sampled'}
    try:
        try:
            record = json.loads(read(receipt))
        except FileNotFoundError:
            record = {}
        if record.get('period') != period:
            if not publish:
                return status
            record = {'period': period, 'sample': cgroups_sample(meta, now), 'sent': False}
            atomic(receipt, record)
        status['observed_at'] = record['sample']['dt']
        if publish and not record['sent']:
            status['status'] = 'delivery_pending'
            send(uri + 'metrics', token, [record['sample']])
            atomic(receipt, dict(record, sent=True))
            record['sent'] = True
        status['status'] = 'sent' if record['sent'] else 'delivery_pending'
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
        if status['status'] != 'delivery_pending':
            status['status'] = 'sample_unavailable'
    return status


def observer_health(meta):
    """Liveness and component health are independent observations."""
    result = {'heartbeat': 'invalid', 'status': 'unavailable', 'degraded_components': []}
    try:
        if meta.get('resource_sampling'):
            marker = json.loads(read(Path('/var/lib/duplika-betterstack-cloudlinux/private/collector-health.json')))
            at = marker.get('at') if isinstance(marker, dict) else None
            if (isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at)):
                return result
            if marker.get('boot_id') is not None:
                boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                if marker['boot_id'] != boot:
                    return dict(result, heartbeat='previous_boot')
            max_age = 180
        else:
            spool = Path('/var/lib/duplika-betterstack-cloudlinux/spool/events.jsonl')
            with spool.open('rb') as stream:
                stream.seek(max(0, spool.stat().st_size - 262144))
                rows = stream.read(262144).splitlines()
            marker = None
            for row in reversed(rows):
                try:
                    value = json.loads(row)
                except ValueError:
                    continue
                if isinstance(value, dict) and value.get('event_kind') == 'collector_heartbeat':
                    marker = value
                    break
            if marker is None:
                return dict(result, heartbeat='missing')
            at = datetime.datetime.fromisoformat(marker['dt']).timestamp()
            max_age = 900
        components = marker.get('components')
        if (not isinstance(components, dict) or not components or len(components) > 32
                or any(not isinstance(k, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', k) for k in components)):
            return result
        age = time.time() - at
        if age < 0:
            return result
        if age >= max_age:
            return dict(result, heartbeat='stale')
        failed = sorted(k for k, v in components.items() if v == 'failed' or v is False)
        return {'heartbeat': 'fresh', 'status': 'degraded' if failed else 'ok',
                'degraded_components': failed}
    except FileNotFoundError:
        return dict(result, heartbeat='missing')
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        return result


def status(publish=False, quiet=False):
    meta = json.loads(read(BASE / 'manifest.json'))
    hashes = snapshot(meta['critical_paths'], meta['config_path'])
    drift = hashes != meta['baseline']
    uri, token = destination(json.loads(read(meta['config_path'])), meta['target'],
                             allow_metrics_only=bool(meta.get('migration_review', {}).get('minimal_host')))
    services = {}
    for name in meta['services']:
        services[name] = health.run(['systemctl', 'is-active', '--quiet', name]).returncode == 0
    # Timers can remain active after a failed oneshot: report last results too.
    for name in meta.get('oneshots', []):
        p = health.run(['systemctl', 'show', name, '-p', 'Result', '--value'])
        services[name + ':last_result'] = p.returncode == 0 and p.stdout.strip() == 'success'
    checks = {}
    if meta.get('migration_review', {}).get('minimal_host'):
        retired = ('duplika-vector-acl.timer', 'duplika-vector-acl.service') if meta['pipeline_variant'] == 'almalinux-wordpress-minimal-v4' else ('duplika-betterstack-acl.timer', 'duplika-betterstack-acl.service')
        for name in retired:
            checks['retired_inactive:' + name] = health.run(['systemctl', 'is-active', '--quiet', name]).returncode in (3, 4)
        checks['retired_timer_disabled'] = health.run(['systemctl', 'is-enabled', retired[0]]).stdout.strip() == 'disabled'
    observer = None
    if meta['config_path'] == '/etc/duplika-betterstack-cloudlinux/vector.json':
        observer = observer_health(meta)
        checks['observer_heartbeat:' + observer['heartbeat']] = observer['heartbeat'] == 'fresh'
    if meta.get('storage_observer'):
        try:
            storage = json.loads(read(Path('/var/lib/duplika-betterstack-cloudlinux/private/storage-health.json')))
            checks['storage_heartbeat'] = (0 <= time.time() - storage.get('at', 0) < 180
                                              and storage.get('probe_failures') == 0)
        except (OSError, ValueError, RuntimeError):
            checks['storage_heartbeat'] = False
    version = health.run([meta['vector_binary'], '--version'])
    vector_version = version.stdout.strip()[:160] if version.returncode == 0 else 'unknown'
    maintenance = health.observe()
    ls_record = health.daily_litespeed(STATE, read, atomic, time.time(), online=publish and not drift)
    event = {'dt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'event_kind': 'collector_identity',
        'host': meta['target']['host'], 'server_ip': meta['target']['server_ip'],
        'source_id': meta['target']['source_id'],
        'template_id': meta.get('base_template_id') or ('cloudlinux-cpanel-multisite' if meta['capabilities']['os_id'] == 'cloudlinux' else 'almalinux-cpanel-wordpress'),
        'installer_version': meta['installer_version'],
        'agent_version': meta['agent_version'], 'template_version': '4', 'configuration_schema': SCHEMA,
        'vector_version': vector_version, 'capabilities': meta['capabilities'],
        'pipeline_variant': meta['pipeline_variant'],
        'drift_detected': drift, 'version_status': 'drift' if drift else 'matches_reviewed_baseline',
        'services_healthy': all(services.values()),
        'inactive_services': [k for k, v in services.items() if not v],
        'failed_health_checks': [k for k, v in checks.items() if not v],
        'maintenance': maintenance, 'litespeed_maintenance': ls_record, 'metrics_delivery_verified': False,
        'release_channel': 'candidate', 'remote_acceptance': 'external_evidence_required'}
    event['collector_healthy'] = (all(services.values()) and all(checks.values())
                                  and (observer is None or observer['status'] == 'ok'))
    if observer is not None:
        event['observer_health'] = observer
    if meta.get('resource_sampling'):
        event['resource_sampling'] = meta['resource_sampling']
    if meta.get('monitoring_capabilities'):
        event['monitoring_capabilities'] = meta['monitoring_capabilities']
    if meta.get('monitoring_policy'):
        event['monitoring_policy'] = meta['monitoring_policy']
    if meta.get('cgroups_inventory') and meta['capabilities']['os_id'] == 'cloudlinux' and meta['capabilities'].get('edition') != 'CloudLinux OS Solo':
        event['cgroups_inventory'] = cgroups_inventory(meta, uri, token, publish and not drift,
                                                       datetime.datetime.now(datetime.timezone.utc))
    if meta.get('migration_review', {}).get('filesystem_aliases'):
        # The host namespace distinguishes actual read-only mounts from service sandbox mounts.
        try:
            with Path('/proc/1/mountinfo').open() as rows:
                flags = {}
                for row in rows:
                    left, sep, right = row.partition(' - ')
                    fields, fs = left.split(), right.split()
                    if sep and len(fields) >= 6 and len(fs) >= 3 and fields[4] in SYSTEM_MOUNTS:
                        flags[fields[4]] = 'ro' in fields[5].split(',') or 'ro' in fs[2].split(',')
            event['mount_readonly'] = flags
        except OSError:
            event['mount_flags_status'] = 'unavailable'
    old = json.loads(read(STATE / 'last-send.json')) if (STATE / 'last-send.json').exists() else {}
    event['host_inventory'] = host_inventory(meta['capabilities'], publish and not drift, old.get('host_inventory_at', ''))
    digest = hashlib.sha256(canonical(hashes)).hexdigest()
    stable = {key: value for key, value in event.items() if key != 'dt'}
    stable['integrity_digest'] = digest
    status_digest = hashlib.sha256(canonical(stable)).hexdigest()
    if digest != old.get('integrity_digest'):
        event['integrity_digest'] = digest
    # Daily explicit inventory is small; no online update/catalog polling on servers.
    if time.time() - old.get('inventory_at', 0) >= 86400:
        event['inventory_daily'] = True
    due = status_digest != old.get('status_digest') or event.get('inventory_daily', False)
    # Daily metrics use their observation timestamp, never a renewed cached time.
    # Delivery acknowledgement is separate from the probe cache: retry on network failure.
    if publish and ls_record.get('checked_at') is not None:
        receipt = STATE / 'maintenance-metrics-send.json'
        sent = json.loads(read(receipt)) if receipt.exists() else {}
        if sent.get('checked_at') != ls_record['checked_at']:
            metrics = health.maintenance_metrics(ls_record, meta['target'])
            send(uri + 'metrics', token, metrics)
            atomic(receipt, {'checked_at': ls_record['checked_at']})
    if publish and due:
        reboot_rows = health.reboot_metrics(maintenance, meta['target'], time.time())
        if reboot_rows:
            send(uri + 'metrics', token, reboot_rows)
        send(uri, token, event)
        atomic(STATE / 'last-send.json', {'integrity_digest': digest,
               'status_digest': status_digest, 'host_inventory_at': event['host_inventory']['observed_at'],
               'inventory_at': time.time() if event.get('inventory_daily') else old.get('inventory_at', 0)})
    if quiet:
        pass
    elif publish and not due:
        print('Identidad sin cambios; próximo envío ante cambios o inventario diario.')
    else:
        print(json.dumps(event, indent=2))
    if drift:
        raise RuntimeError('integrity_failure')
    if not all(services.values()):
        raise RuntimeError('service_failure')
    if not all(checks.values()):
        raise RuntimeError('health_check_failure')
    if observer is not None and observer['status'] == 'degraded':
        raise RuntimeError('collector_components_degraded')
    return event


if __name__ == '__main__':
    import sys
    try:
        p = argparse.ArgumentParser()
        p.add_argument('--publish', action='store_true')
        status(p.parse_args().publish)
    except Exception as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        sys.exit(1)
