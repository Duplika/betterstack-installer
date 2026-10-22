"""Hourly integrity/maintenance identity. No application hashes or raw logs."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
from capabilities import VERSION, SCHEMA
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


def destination(config, target):
    tokens = set()
    paths = set()
    origin = 'https://' + target['ingesting_host']
    for sink in config.get('sinks', {}).values():
        if sink.get('uri') not in (origin + '/', origin + '/metrics') or sink.get('auth', {}).get('strategy') != 'bearer':
            raise RuntimeError('unexpected_destination')
        paths.add(sink['uri'])
        tokens.add(sink['auth']['token'])
    if paths != {origin + '/', origin + '/metrics'} or len(tokens) != 1:
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


def status(publish=False, quiet=False):
    meta = json.loads(read(BASE / 'manifest.json'))
    hashes = snapshot(meta['critical_paths'], meta['config_path'])
    drift = hashes != meta['baseline']
    uri, token = destination(json.loads(read(meta['config_path'])), meta['target'])
    services = {}
    for name in meta['services']:
        services[name] = health.run(['systemctl', 'is-active', '--quiet', name]).returncode == 0
    # Timers can remain active after a failed oneshot: report last results too.
    for name in meta.get('oneshots', []):
        p = health.run(['systemctl', 'show', name, '-p', 'Result', '--value'])
        services[name + ':last_result'] = p.returncode == 0 and p.stdout.strip() == 'success'
    if meta['config_path'] == '/etc/duplika-betterstack-cloudlinux/vector.json':
        if meta.get('resource_sampling'):
            try:
                heartbeat = json.loads(read(Path('/var/lib/duplika-betterstack-cloudlinux/private/collector-health.json')))
                services['observer_heartbeat'] = (0 <= time.time() - heartbeat.get('at', 0) < 180
                    and 'failed' not in heartbeat.get('components', {}).values())
            except (OSError, ValueError, RuntimeError):
                services['observer_heartbeat'] = False
        else:
            spool = Path('/var/lib/duplika-betterstack-cloudlinux/spool/events.jsonl')
            try:
                with spool.open('rb') as stream:
                    stream.seek(max(0, spool.stat().st_size - 262144))
                    rows = stream.read(262144).splitlines()
                heartbeat = next(json.loads(row) for row in reversed(rows) if b'collector_heartbeat' in row and json.loads(row).get('event_kind') == 'collector_heartbeat')
                event_time = datetime.datetime.fromisoformat(heartbeat['dt']).timestamp()
                fresh = 0 <= time.time() - event_time < 900
                services['observer_heartbeat'] = fresh and 'failed' not in heartbeat.get('components', {}).values()
            except (OSError, ValueError, StopIteration):
                services['observer_heartbeat'] = False
    if meta.get('storage_observer'):
        try:
            storage = json.loads(read(Path('/var/lib/duplika-betterstack-cloudlinux/private/storage-health.json')))
            services['storage_heartbeat'] = (0 <= time.time() - storage.get('at', 0) < 180
                                              and storage.get('probe_failures') == 0)
        except (OSError, ValueError, RuntimeError):
            services['storage_heartbeat'] = False
    version = health.run([meta['vector_binary'], '--version'])
    vector_version = version.stdout.strip()[:160] if version.returncode == 0 else 'unknown'
    maintenance = health.observe()
    event = {'dt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'event_kind': 'collector_identity',
        'host': meta['target']['host'], 'server_ip': meta['target']['server_ip'],
        'source_id': meta['target']['source_id'],
        'template_id': 'cloudlinux-cpanel-multisite' if meta['capabilities']['os_id'] == 'cloudlinux' else 'almalinux-cpanel-wordpress',
        'installer_version': meta['installer_version'],
        'agent_version': meta['agent_version'], 'template_version': '4', 'configuration_schema': SCHEMA,
        'vector_version': vector_version, 'capabilities': meta['capabilities'],
        'pipeline_variant': meta['pipeline_variant'],
        'drift_detected': drift, 'version_status': 'drift' if drift else 'matches_reviewed_baseline',
        'services_healthy': all(services.values()),
        'inactive_services': [k for k, v in services.items() if not v],
        'maintenance': maintenance, 'metrics_delivery_verified': False,
        'release_channel': 'candidate', 'remote_acceptance': 'external_evidence_required'}
    if meta.get('resource_sampling'):
        event['resource_sampling'] = meta['resource_sampling']
    old = json.loads(read(STATE / 'last-send.json')) if (STATE / 'last-send.json').exists() else {}
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
    if publish and due:
        send(uri, token, event)
        atomic(STATE / 'last-send.json', {'integrity_digest': digest,
               'status_digest': status_digest,
               'inventory_at': time.time() if event.get('inventory_daily') else old.get('inventory_at', 0)})
    if quiet:
        pass
    elif publish and not due:
        print('Identidad sin cambios; próximo envío ante cambios o inventario diario.')
    else:
        print(json.dumps(event, indent=2))
    if drift or not all(services.values()):
        raise RuntimeError('integrity_or_service_failure')
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
