#!/usr/bin/env python3
"""Bounded metadata-only volume, Linux MD and optional SMART observations.

No directory walks, application reads, self-tests, power changes or network calls.
The short-lived service sees real volumes read-only; transport remains hidden from home.
"""
import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import re
import resource
import shutil
import stat
import subprocess
import sys
import time

ROOT = Path('/var/lib/duplika-betterstack-cloudlinux')
BASE = Path('/etc/duplika-betterstack-cloudlinux')
LOCAL_FS = {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'vfat', 'exfat', 'f2fs', 'ntfs', 'ntfs3', 'zfs'}
EXCLUDED_FS = {'tmpfs', 'devtmpfs', 'proc', 'sysfs', 'overlay', 'squashfs', 'nfs', 'nfs4', 'cifs', 'smb3', '9p', 'autofs'}
ALIASES = ('/home/virtfs', '/var/cagefs', '/usr/share/cagefs-skeleton')
SMART_INTERVAL = 21600
MAX_DEVICES = 128
MAX_VOLUMES = 256


def unescape(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def volumes(text):
    """Deduplicate by filesystem device, preferring the canonical root mount.

A CageFS alias never excludes the real device. Subvolumes share filesystem capacity.
"""
    grouped = {}
    for line in text.splitlines():
        left, sep, right = line.partition(' - ')
        if not sep:
            continue
        a, b = left.split(), right.split()
        if (len(a) < 6 or len(b) < 3 or b[0] in EXCLUDED_FS
                or (b[0] not in LOCAL_FS and not b[1].startswith('/dev/'))):
            continue
        target, source = unescape(a[4]), unescape(b[1])
        if not target.startswith('/') or '\n' in target or len(target) > 512:
            continue
        alias = any(target == root or target.startswith(root + '/') for root in ALIASES)
        item = {'device': source, 'mountpoint': target, 'fstype': b[0],
                'readonly': 'ro' in a[5].split(',') or 'ro' in b[2].split(','),
                'device_id': a[2]}
        rank = (alias, a[3] != '/', len(target), target)
        key = (a[2], b[0])
        if key not in grouped or rank < grouped[key][0]:
            grouped[key] = (rank, item)
    # Never publish customer-specific CageFS aliases when no canonical mount is visible.
    result = [item for rank, item in grouped.values() if not rank[0]]
    if len(result) > MAX_VOLUMES:
        raise RuntimeError('volume_inventory_limit')
    return sorted(result, key=lambda item: item['mountpoint'])


def volume_values(item, statvfs=None):
    if statvfs is not None:
        v = statvfs(item['mountpoint'])
    else:
        fd = os.open(item['mountpoint'], os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            dev = os.fstat(fd).st_dev
            if item.get('device_id') != str(os.major(dev)) + ':' + str(os.minor(dev)):
                raise OSError('volume_changed_or_hidden_by_namespace')
            v = os.statvfs('/proc/self/fd/' + str(fd))
        finally:
            os.close(fd)
    size = v.f_frsize or v.f_bsize
    total, free, available = v.f_blocks * size, v.f_bfree * size, max(0, v.f_bavail) * size
    used = max(0, total - free)
    values = {'total_bytes': total, 'used_bytes': used, 'available_bytes': available,
              'readonly': int(item['readonly'])}
    # df-style ratio uses space available to non-root rather than reserved blocks.
    if used + available:
        values['used_ratio'] = used / (used + available)
    if v.f_files > 0:
        values.update(inodes_total=v.f_files, inodes_free=v.f_ffree,
                      inodes_used_ratio=max(0, v.f_files - v.f_ffree) / v.f_files)
    return values


def raid_arrays(text):
    result = []
    for block in re.split(r'\n(?=\S)', text):
        match = re.match(r'(md\S+)\s*:\s*(.*)', block, re.S)
        if not match:
            continue
        name, body = match.groups()
        members = re.search(r'\[(\d+)/(\d+)\]\s+\[([U_]+)\]', body)
        result.append({'array': name, 'active': bool(re.match(r'active\s', body)),
                       'expected': int(members[1]) if members else None,
                       'present': int(members[2]) if members else None,
                       'degraded': '_' in members[3] if members else None,
                       'syncing': bool(re.search(r'\b(resync|recovery|reshape|check|repair)\s*=', body))})
    return result


def devices(sysblock=Path('/sys/block')):
    rows = []
    for path in sorted(sysblock.iterdir()):
        if not re.fullmatch(r'(sd[a-z]+|hd[a-z]+|nvme\d+n\d+|vd[a-z]+|xvd[a-z]+)', path.name):
            continue
        vendor = ''
        try:
            vendor = (path / 'device/vendor').read_text().strip()
        except OSError:
            pass
        kind = 'nvme' if path.name.startswith('nvme') else 'ata' if vendor == 'ATA' else None
        rows.append({'device': '/dev/' + path.name, 'kind': kind})
    if len(rows) > MAX_DEVICES:
        raise RuntimeError('device_inventory_limit')
    return rows


def executable(name):
    value = shutil.which(name, path='/usr/sbin:/usr/bin:/sbin:/bin')
    if not value:
        return None
    path = Path(value).resolve()
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode & 0o022:
        raise RuntimeError('unsafe_storage_executable')
    return str(path)


def smart_values(data, returncode):
    # smartctl exit status is a bitmask, not a generic success/failure boolean.
    if returncode < 0 or returncode & 7:
        return 'unavailable', {}
    result = {}
    passed = data.get('smart_status', {}).get('passed')
    if isinstance(passed, bool):
        result['healthy'] = int(passed)
    temperature = data.get('temperature', {}).get('current')
    if isinstance(temperature, (int, float)):
        result['temperature_celsius'] = temperature
    nvme = data.get('nvme_smart_health_information_log') or {}
    for key in ('critical_warning', 'available_spare', 'available_spare_threshold', 'percentage_used', 'media_errors', 'num_err_log_entries'):
        if isinstance(nvme.get(key), (int, float)):
            result['nvme_' + key] = nvme[key]
    # Only standard raw sector counts. Vendor-specific SSD wear values are not guessed.
    names = {5: 'reallocated_sectors', 197: 'pending_sectors', 198: 'offline_uncorrectable_sectors'}
    for row in (data.get('ata_smart_attributes') or {}).get('table', []):
        if row.get('id') in names and isinstance((row.get('raw') or {}).get('value'), (int, float)):
            result[names[row['id']]] = row['raw']['value']
    result['exit_status'] = returncode
    return ('observed' if 'healthy' in result else 'unknown'), result


def probe_smart(item, runner=subprocess.run):
    # Do not autodetect ATA/SCSI bridges: identification can wake sleeping devices.
    if item['kind'] not in ('ata', 'nvme'):
        return 'unsupported_transport', {}
    tool = executable('smartctl')
    if not tool:
        return 'tool_unavailable', {}
    args = [tool, '-j', '-d', item['kind']]
    if item['kind'] == 'ata':
        args += ['-n', 'standby,3']
    args += ['-H', '-A', item['device']]
    p = runner(args, capture_output=True, text=True, timeout=5,
               env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    if len(p.stdout) > 1024 * 1024:
        return 'invalid_response', {}
    try:
        data = json.loads(p.stdout)
    except ValueError:
        return 'invalid_response', {}
    if not isinstance(data, dict):
        return 'invalid_response', {}
    if p.returncode == 3 and data.get('power_mode') in ('STANDBY', 'SLEEP'):
        return 'sleeping', {}
    return smart_values(data, p.returncode)


class Output:
    def __init__(self, profile):
        self.profile = profile
        self.at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.metrics = []
        self.logs = []

    def metric(self, name, value, **tags):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return
        self.metrics.append({'dt': self.at, 'name': 'duplika_storage_' + name,
            'gauge': {'value': value}, 'tags': dict(host=self.profile['host'],
                server_ip=self.profile['server_ip'], **{k: str(v) for k, v in tags.items()})})

    def event(self, state, now, signature):
        # Report changes and daily inventory, never raw tool output/serial/WWN.
        old = state.get('event', {})
        if signature != old.get('signature') or not 0 <= now - old.get('at', 0) < 86400:
            self.logs.append({'dt': self.at, 'host': self.profile['host'],
                'server_ip': self.profile['server_ip'], 'event_kind': 'storage_status',
                'service': 'storage_observer', **signature})
            state['event'] = {'signature': signature, 'at': now}

    def flush(self):
        for name, rows in (('metrics.jsonl.storage', self.metrics), ('events.jsonl.storage', self.logs)):
            if not rows:
                continue
            path = ROOT / 'spool' / name
            if path.exists() and path.stat().st_size >= 1024 * 1024:
                path.with_name(name + '.2').unlink(missing_ok=True)
                if path.with_name(name + '.1').exists():
                    path.with_name(name + '.1').rename(path.with_name(name + '.2'))
                path.rename(path.with_name(name + '.1'))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o640)
            with os.fdopen(fd, 'w') as stream:
                os.fchown(stream.fileno(), 0, self.profile['reader_gid'])
                os.fchmod(stream.fileno(), 0o640)
                for row in rows:
                    stream.write(json.dumps(row, separators=(',', ':')) + '\n')


def atomic(path, value):
    temporary = path.with_suffix('.storage-tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, separators=(',', ':'))
    os.replace(temporary, path)


def main():
    started, now = time.monotonic(), time.time()
    private = ROOT / 'private'
    fd = os.open(private / 'storage.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w'):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = private / 'storage-state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        out = Output(json.loads((BASE / 'profile.json').read_text()))
        failed = []
        # PID 1 mount options describe the host, not our intentional read-only remounts.
        inventory = volumes(Path('/proc/1/mountinfo').read_text())
        if not inventory:
            raise RuntimeError('no_local_volumes_visible')
        for item in inventory:
            tags = {k: item[k] for k in ('device', 'mountpoint', 'fstype')}
            try:
                for key, value in volume_values(item).items():
                    out.metric('volume_' + key, value, **tags)
                out.metric('volume_probe_ok', 1, **tags)
            except OSError:
                failed.append(item['mountpoint'])
                out.metric('volume_probe_ok', 0, **tags)
        mdstat = Path('/proc/mdstat')
        arrays = raid_arrays(mdstat.read_text()) if mdstat.exists() else []
        for item in arrays:
            for key in ('active', 'expected', 'present', 'degraded', 'syncing'):
                if item[key] is not None:
                    out.metric('raid_' + key, int(item[key]), array=item['array'])
        disks = devices()
        observations = state.setdefault('smart', {})
        live = {item['device'] for item in disks}
        observations = {key: value for key, value in observations.items() if key in live}
        state['smart'] = observations
        # One short probe per cycle; no startup burst, no mandatory packages.
        due = [item for item in disks if not 0 <= now - observations.get(item['device'], {}).get('attempt_at', 0) < SMART_INTERVAL]
        if due:
            item = min(due, key=lambda row: observations.get(row['device'], {}).get('attempt_at', 0))
            try:
                status, values = probe_smart(item)
            except (OSError, subprocess.TimeoutExpired, RuntimeError):
                status, values = 'probe_failed', {}
            observations[item['device']] = {'attempt_at': now, 'status': status, 'values': values}
            for key, value in values.items():
                out.metric('smart_' + key, value, device=item['device'])
            out.metric('smart_sample_timestamp_seconds', now, device=item['device'])
        for item in disks:
            sample = observations.get(item['device'], {})
            out.metric('smart_observed', int(sample.get('status') == 'observed'), device=item['device'])
            if 'attempt_at' in sample:
                out.metric('smart_attempt_age_seconds', max(0, now - sample['attempt_at']), device=item['device'])
        signature = {'volumes': inventory, 'volume_probe_failures': failed, 'raid': arrays,
                     'smart': {item['device']: {'status': observations.get(item['device'], {}).get('status', 'pending'),
                         'health': {k: v for k, v in observations.get(item['device'], {}).get('values', {}).items() if k != 'temperature_celsius'}} for item in disks}}
        out.event(state, now, signature)
        out.metric('device_count', len(disks))
        out.metric('raid_array_count', len(arrays))
        out.metric('volume_count', len(inventory))
        out.metric('probe_failures', len(failed))
        out.metric('duration_seconds', time.monotonic() - started)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
        out.metric('cpu_with_children_seconds', usage.ru_utime + usage.ru_stime + children.ru_utime + children.ru_stime)
        out.metric('peak_rss_bytes', usage.ru_maxrss * 1024)
        out.flush()
        atomic(state_path, state)
        atomic(private / 'storage-health.json', {'at': now, 'probe_failures': len(failed)})
        if failed:
            raise RuntimeError('storage_volume_probe_failed')


def preflight():
    items = volumes(Path('/proc/1/mountinfo').read_text())
    if not items:
        raise RuntimeError('no_local_volumes_visible')
    result = []
    for item in items:
        result.append(dict(item, **volume_values(item)))
    mdstat = Path('/proc/mdstat')
    print(json.dumps({'volumes': result, 'raid': raid_arrays(mdstat.read_text()) if mdstat.exists() else [],
        'devices': devices(), 'smartctl_available': executable('smartctl') is not None,
        'application_files_read': False, 'persistent_changes_applied': False}, indent=2))


if __name__ == '__main__':
    if sys.argv[1:] == ['--probe']:
        preflight()
    elif sys.argv[1:]:
        raise SystemExit('unsupported_storage_arguments')
    else:
        main()
