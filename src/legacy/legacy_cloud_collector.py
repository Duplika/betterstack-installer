#!/usr/bin/env python3
"""Read-only CloudLinux/cPanel observer. No application files or SQL text are read."""
import collections
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import time

BASE = Path('/etc/duplika-betterstack-cloudlinux')
ROOT = Path('/var/lib/duplika-betterstack-cloudlinux')
STATE = ROOT / 'private'
SPOOL = ROOT / 'spool'
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_CYCLE_BYTES = 32 * 1024 * 1024
ACCESS = re.compile(r'^\S+\s+\S+\s+\S+\s+\[[^\]]+\]\s+"(?P<method>[A-Z]+)\s+(?P<url>[^\s"]+)\s+HTTP/[^"\s]+"\s+(?P<status>\d{3})\s+(?P<bytes>\d+|-)(?:\s+"[^"\n]*"\s+"(?P<ua>[^"\n]*)")?')
DOMAIN = re.compile(r'^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$')
USER = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')
TEMPLATE_VERSION = 2


class LogPolicy:
    """Thin only metric-backed summaries; preserve metadata transitions and pressure."""
    def __init__(self, state, now):
        self.now = now
        self.entries = state.setdefault('log_policy_v2', {})
        for key, entry in list(self.entries.items()):
            if now - entry.get('seen_at', 0) > 86400:
                del self.entries[key]

    def decision(self, kind, fields):
        if kind not in ('account_resources', 'lve_usage'):
            return 'unfiltered'
        key = kind + ':' + str(fields.get('user', ''))
        if kind == 'account_resources':
            # running_processes and observation flags are not duplicated in account metrics.
            signature = {k: fields.get(k) for k in ('cpanel_account', 'measurement',
                'cpu_observed', 'io_observed', 'running_processes', 'blocked_processes')}
            pressure = (fields.get('blocked_processes') or 0) > 0
        else:
            usage, limits = fields.get('usage') or {}, fields.get('limits') or {}
            signature = {k: fields.get(k) for k in ('lve_id', 'limits', 'mysql_governor')}
            signature['usage_fields'] = sorted(usage)
            signature['mysql_fields'] = sorted(fields.get('mysql') or {})
            # Keep every sample near any configured LVE limit, including zero-CPU I/O pressure.
            pressure = any(number(limit) is not None and float(limit) > 0 and
                number(usage.get(k)) is not None and float(usage[k]) / float(limit) >= 0.8
                for k, limit in limits.items())
        signature['pressure'] = pressure
        old = self.entries.get(key)
        reason = None
        if not old:
            reason = 'first_observation'
        elif self.now < old['emitted_at']:
            reason = 'clock_reset'
        elif signature != old['signature']:
            reason = 'metadata_or_pressure_change'
        elif pressure:
            reason = 'resource_pressure'
        elif self.now - old['emitted_at'] >= 300:
            reason = 'periodic_5m'
        if reason:
            self.entries[key] = {'emitted_at': self.now, 'seen_at': self.now,
                                 'signature': signature}
        else:
            old['seen_at'] = self.now
        return reason


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, separators=(',', ':'))
    os.replace(temp, path)


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def command(args, timeout=8):
    # No shell, fixed root-owned executables, stdout never included in failure events.
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    if result.returncode:
        raise RuntimeError('command_failed')
    if len(result.stdout) > 8 * 1024 * 1024:
        raise RuntimeError('command_output_too_large')
    return result.stdout


def inventory():
    domains = {}
    for line in Path('/etc/userdomains').read_text().splitlines():
        domain, sep, user = line.partition(':')
        domain, user = domain.strip().lower(), user.strip()
        if sep and DOMAIN.fullmatch(domain) and USER.fullmatch(user):
            domains[domain] = user
    accounts = set(domains.values())
    users = {p.pw_uid: p.pw_name for p in pwd.getpwall()}
    return domains, accounts, users


def proc_snapshot(proc=Path('/proc')):
    result = {}
    ticks = os.sysconf('SC_CLK_TCK')
    pages = os.sysconf('SC_PAGE_SIZE')
    for path in proc.iterdir():
        if not path.name.isdigit():
            continue
        try:
            raw = (path / 'stat').read_text()
            left, right = raw.index('('), raw.rindex(')')
            fields = raw[right + 2:].split()
            uid = int(re.search(r'^Uid:\s+\d+\s+(\d+)', (path / 'status').read_text(), re.M)[1])
            io = {}
            try:
                io = dict(line.split(':', 1) for line in (path / 'io').read_text().splitlines())
            except OSError:
                pass
            # comm only: never capture command arguments, environment or /proc/*/cmdline.
            comm = re.sub(r'[^A-Za-z0-9_.:+ -]', '?', raw[left + 1:right])[:80]
            result[path.name] = {'pid': int(path.name), 'uid': uid, 'command': comm,
                                'state': fields[0], 'ppid': int(fields[1]),
                                'ticks': int(fields[11]) + int(fields[12]),
                                'start_ticks': int(fields[19]),
                                'rss_bytes': max(0, int(fields[21])) * pages,
                                'read_bytes': int(io['read_bytes']) if 'read_bytes' in io else None,
                                'write_bytes': int(io['write_bytes']) if 'write_bytes' in io else None}
        except (OSError, ValueError, IndexError, TypeError):
            continue  # Processes may exit between procfs reads.
    return result, ticks


def process_deltas(current, previous, elapsed, hz, old_uptime):
    rows = []
    for pid, item in current.items():
        row = dict(item)
        old = previous.get(pid)
        same = old and old['start_ticks'] == item['start_ticks'] and old['uid'] == item['uid']
        new = old_uptime is not None and item['start_ticks'] / hz >= old_uptime
        valid = elapsed > 0 and (same or new)
        for dest, src, scale in [('cpu_percent', 'ticks', 100.0 / hz),
                                 ('read_bytes_per_second', 'read_bytes', 1),
                                 ('write_bytes_per_second', 'write_bytes', 1)]:
            baseline = old.get(src) if same else 0
            row[dest] = (max(0, item[src] - baseline) * scale / elapsed
                         if valid and item[src] is not None and baseline is not None else None)
        rows.append(row)
    return rows


class Output:
    def __init__(self, profile, state=None, now=None):
        self.profile = profile
        self.logs, self.metrics = [], []
        self.at = timestamp()
        self.policy = LogPolicy(state if state is not None else {}, time.time() if now is None else now)
        self.suppressed = collections.Counter()

    def log(self, kind, **fields):
        reason = self.policy.decision(kind, fields)
        if reason is None:
            self.suppressed[kind] += 1
            return
        if reason != 'unfiltered':
            fields['log_reason'] = reason
        self.logs.append(dict(dt=self.at, host=self.profile['host'],
                              server_ip=self.profile['server_ip'], event_kind=kind,
                              service='cloudlinux_observer', template_version=TEMPLATE_VERSION,
                              level=fields.pop('level', 'info'), **fields))

    def metric(self, name, value, **labels):
        value = number(value)
        if value is None:
            return
        self.metrics.append({'dt': self.at, 'name': name, 'gauge': {'value': value},
                             'tags': dict(host=self.profile['host'], server_ip=self.profile['server_ip'],
                                          **{k: str(v) for k, v in labels.items()})})

    def flush(self):
        # Bounded local retention: current + 2 rotated files, 16 MiB each, per stream.
        for filename, rows in [('events.jsonl', self.logs), ('metrics.jsonl', self.metrics)]:
            path = SPOOL / filename
            if path.exists() and path.stat().st_size > 16 * 1024 * 1024:
                for i in (2, 1):
                    previous = path.with_name(filename + '.' + str(i))
                    if i == 2:
                        previous.unlink(missing_ok=True)
                    else:
                        if previous.exists():
                            previous.rename(path.with_name(filename + '.2'))
                path.rename(path.with_name(filename + '.1'))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o640)
            os.fchown(fd, 0, self.profile['reader_gid'])
            os.fchmod(fd, 0o640)
            with os.fdopen(fd, 'a') as stream:
                for row in rows:
                    stream.write(json.dumps(row, separators=(',', ':')) + '\n')


def collect_processes(out, state, domains, accounts, users):
    current, hz = proc_snapshot()
    uptime = float(Path('/proc/uptime').read_text().split()[0])
    old_uptime = state.get('proc_uptime')
    elapsed = uptime - old_uptime if old_uptime is not None else 0
    previous = state.get('proc', {}) if 0 < elapsed < 600 else {}
    rows = process_deltas(current, previous, elapsed, hz, old_uptime if previous else None)
    grouped = collections.defaultdict(lambda: {'cpu_percent': 0, 'cpu_observed': False,
        'rss_bytes': 0, 'process_count': 0, 'blocked_processes': 0, 'running_processes': 0,
        'read_bytes_per_second': 0, 'write_bytes_per_second': 0, 'io_observed': False})
    for row in rows:
        row['user'] = users.get(row['uid'], 'uid-' + str(row['uid']))
        g = grouped[row['user']]
        g['process_count'] += 1
        g['rss_bytes'] += row['rss_bytes']
        g['blocked_processes'] += row['state'] == 'D'
        g['running_processes'] += row['state'] == 'R'
        if row['cpu_percent'] is not None:
            g['cpu_percent'] += row['cpu_percent']
            g['cpu_observed'] = True
        if row['read_bytes_per_second'] is not None:
            g['io_observed'] = True
            for name in ('read_bytes_per_second', 'write_bytes_per_second'):
                g[name] += row[name] or 0
    for user, g in grouped.items():
        is_account = user in accounts
        if not g['cpu_observed']:
            g['cpu_percent'] = None
        if not g['io_observed']:
            g['read_bytes_per_second'] = g['write_bytes_per_second'] = None
        out.log('account_resources', user=user, cpanel_account=is_account,
                interval_seconds=round(elapsed, 3), measurement='sampled_live_processes', **g)
        for key in ('cpu_percent', 'rss_bytes', 'process_count', 'blocked_processes',
                    'read_bytes_per_second', 'write_bytes_per_second'):
            out.metric('duplika_account_' + key, g[key], user=user, cpanel_account=str(is_account).lower())
    # Union of leaders: disk-bound processes are retained even with little CPU.
    selected = {}
    for key in ('cpu_percent', 'rss_bytes', 'read_bytes_per_second', 'write_bytes_per_second'):
        for row in sorted(rows, key=lambda p: p[key] or 0, reverse=True)[:10]:
            selected[row['pid']] = row
    for row in [p for p in rows if p['state'] == 'D'][:20]:
        selected[row['pid']] = row
    for row in selected.values():
        data = {k: row[k] for k in ('pid', 'ppid', 'uid', 'user', 'command', 'state', 'rss_bytes',
                                    'cpu_percent', 'read_bytes_per_second', 'write_bytes_per_second')}
        out.log('process_sample', interval_seconds=round(elapsed, 3), **data)
    out.metric('duplika_processes_blocked', sum(r['state'] == 'D' for r in rows))
    out.metric('duplika_processes_running', sum(r['state'] == 'R' for r in rows))
    state['proc'], state['proc_uptime'] = current, uptime
    return len(rows)


def lve_values(record, section):
    value = record.get(section) or {}
    result = {'cpu_percent': number((value.get('cpu') or {}).get('all')),
              'io_bytes_per_second': number((value.get('io') or {}).get('all')),
              'iops': number(value.get('iops')), 'memory_bytes': number(value.get('mem')),
              'entry_processes': number(value.get('ep')), 'processes': number(value.get('pno'))}
    return {k: v for k, v in result.items() if v is not None}


def collect_lve(out, profile):
    command_path = profile.get('cloudlinux_top')
    if not command_path:
        out.log('collector_unavailable', component='cloudlinux_top', level='warning')
        return False
    args = [command_path, '--json', '--order-by=cpu', '--max=10000']
    if profile.get('isolates_supported'):
        args.append('--domains')
    data = json.loads(command(args, timeout=15))
    if data.get('result') != 'success' or not isinstance(data.get('users'), list):
        raise RuntimeError('cloudlinux_top_invalid_response')
    for record in data['users']:
        user = str(record.get('username') or record.get('id'))[:64]
        usage, limits = lve_values(record, 'usage'), lve_values(record, 'limit')
        mysql = {}
        if data.get('mySqlGov') == 'enabled':
            for key, unit in [('cpu', 'cpu_percent'), ('io', 'io_bytes_per_second')]:
                value = number(((record.get('usage') or {}).get(key) or {}).get('mysql'))
                if value is not None:
                    mysql[unit] = value
                    out.metric('duplika_mysql_' + unit, value, user=user)
        out.log('lve_usage', user=user, lve_id=record.get('id'), usage=usage, limits=limits,
                mysql= mysql, mysql_governor=data.get('mySqlGov', 'unavailable'))
        for key, value in usage.items():
            out.metric('duplika_lve_' + key, value, user=user)
        for key, value in limits.items():
            if value > 0 and key in usage:
                out.metric('duplika_lve_limit_ratio', usage[key] / value, user=user, resource=key)
        for domain in record.get('domains') or []:
            site = domain.get('name')
            if site and DOMAIN.fullmatch(site):
                usage = lve_values(domain, 'usage')
                out.log('isolated_domain_usage', user=user, site=site, usage=usage,
                        limits=lve_values(domain, 'limit'))
                for key, value in usage.items():
                    out.metric('duplika_isolated_domain_' + key, value, user=user, site=site)
    out.metric('duplika_lve_accounts_observed', len(data['users']))
    return True


def route_group(url):
    # Query strings, credentials and literal user-specific paths never leave the host.
    path = url.split('?', 1)[0].split('#', 1)[0].lower()
    if path == '/':
        return 'home'
    if re.search(r'\.(?:css|js|png|jpg|jpeg|webp|gif|ico|svg|woff2?|ttf|mp4|pdf)$', path):
        return 'static'
    if path == '/wp-login.php': return 'login'
    if path == '/xmlrpc.php': return 'xmlrpc'
    if path == '/wp-cron.php': return 'cron'
    if path == '/wp-admin/admin-ajax.php': return 'ajax'
    if path.startswith(('/wp-json/', '/api/')): return 'api'
    if path.startswith(('/categoria-producto/', '/product-category/', '/tienda', '/shop')): return 'catalog'
    if path.startswith(('/carrito', '/cart', '/checkout', '/finalizar-compra')): return 'checkout'
    if path.endswith('.php'): return 'other_php'
    return 'other_dynamic'


def agent_group(ua):
    lower = ua.lower()
    for name in ('googlebot', 'bingbot', 'bytespider', 'meta-webindexer', 'claudebot',
                 'gptbot', 'ahrefsbot', 'semrushbot', 'python', 'curl', 'wget'):
        if name in lower:
            return name
    chrome = re.search(r'Chrome/(\d{1,3})\.', ua)
    if chrome:
        return ('mac_chrome_' if 'Macintosh' in ua else 'chrome_') + chrome[1]
    return 'other'


def read_tail(path, previous, allowance):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError('not_regular_file')
        identity = [info.st_dev, info.st_ino]
        if previous is None:
            return [], {'inode': identity, 'offset': info.st_size}, {'initialized': True, 'bytes_read': 0, 'skipped_bytes': 0}
        rotated = previous.get('inode') != identity or previous['offset'] > info.st_size
        offset = 0 if rotated else previous['offset']
        amount = max(0, min(MAX_FILE_BYTES, allowance))
        skipped = max(0, info.st_size - offset - amount)
        offset += skipped
        stream.seek(offset)
        data = stream.read(min(amount, info.st_size - offset))
        end = data.rfind(b'\n')
        if end < 0:
            # An oversized unterminated line must not consume each future cycle.
            advance = len(data) if len(data) >= MAX_FILE_BYTES else 0
            lines = []
            skipped += advance
        else:
            advance = end + 1
            lines = data[:advance].splitlines()
            if skipped and lines:
                lines = lines[1:]  # The bounded tail may start mid-line.
        return lines, {'inode': identity, 'offset': offset + advance}, {
            'rotated': rotated, 'bytes_read': len(data), 'skipped_bytes': skipped}


def collect_access(out, state, domains, profile):
    old = state.get('access_files', {})
    new, seen, coverage = {}, set(), collections.Counter()
    sites = collections.defaultdict(collections.Counter)
    groups = collections.defaultdict(collections.Counter)
    budget = MAX_CYCLE_BYTES
    # Scan only canonical top-level cPanel domlogs, never /home/virtfs or user symlinks.
    for root in profile['domlog_roots']:
        for path in sorted(Path(root).iterdir()):
            site = path.name.removesuffix('-ssl_log')
            if site not in domains or path.is_symlink() or not path.is_file():
                continue
            try:
                info = path.stat()
                key = (info.st_dev, info.st_ino)
                if key in seen:
                    continue
                seen.add(key)
                lines, position, quality = read_tail(path, old.get(str(path)), budget)
                new[str(path)] = position
                budget -= quality['bytes_read']
                coverage.update(quality)
                coverage['files'] += 1
                for line in lines:
                    if len(line) > 16384:
                        coverage['oversized_lines'] += 1
                        continue
                    match = ACCESS.match(line.decode('utf-8', errors='replace'))
                    if not match:
                        coverage['unparsed_lines'] += 1
                        continue
                    values = match.groupdict()
                    group = sites[site]
                    group['requests'] += 1
                    group['response_bytes'] += int(values['bytes']) if values['bytes'].isdigit() else 0
                    group['errors_5xx'] += values['status'].startswith('5')
                    group['errors_4xx'] += values['status'].startswith('4')
                    group['post_requests'] += values['method'] == 'POST'
                    route = route_group(values['url'])
                    group['dynamic_requests'] += route != 'static'
                    groups[site]['route:' + route] += 1
                    groups[site]['agent:' + agent_group(values.get('ua') or '')] += 1
                    groups[site]['status:' + values['status']] += 1
            except (OSError, ValueError, RuntimeError):
                coverage['unreadable_files'] += 1
    complete = not any(coverage[k] for k in ('skipped_bytes', 'rotated', 'initialized', 'unparsed_lines', 'unreadable_files', 'oversized_lines'))
    elapsed = max(1, time.time() - state.get('access_at', time.time()))
    for site, counts in sites.items():
        out.log('site_traffic', site=site, user=domains[site], interval_seconds=round(elapsed, 3),
                coverage_complete=complete, breakdown=dict(groups[site]), **counts)
        for key in ('requests', 'dynamic_requests', 'errors_5xx', 'errors_4xx', 'response_bytes'):
            out.metric('duplika_site_' + key + '_per_second', counts[key] / elapsed,
                       site=site, user=domains[site])
    out.log('access_log_coverage', coverage_complete=complete, **dict(coverage))
    out.metric('duplika_access_skipped_bytes', coverage['skipped_bytes'])
    out.metric('duplika_access_parse_failures', coverage['unparsed_lines'])
    state['access_files'], state['access_at'] = new, time.time()
    return dict(coverage)


def collect_errors(out, state, domains, profile):
    positions = state.get('error_files', {})
    for path_text in profile['error_logs']:
        try:
            lines, pos, quality = read_tail(Path(path_text), positions.get(path_text), 1024 * 1024)
            positions[path_text] = pos
            counts = collections.Counter()
            examples = {}
            for line in lines:
                msg = line[:16384].decode('utf-8', errors='replace')
                kind = None
                if re.search(r'(?i)Abort request processing by PID', msg): kind = 'lsapi_abort'
                elif re.search(r'(?i)(out of memory|cannot allocate memory)', msg): kind = 'memory_allocation_failure'
                elif re.search(r'(?i)(segfault|segmentation fault|corrupt|double free)', msg): kind = 'native_crash'
                elif re.search(r'(?i)(max.?children|max.?connections|reached.*max)', msg): kind = 'worker_limit'
                elif re.search(r'(?i)(timed out|timeout)', msg): kind = 'timeout'
                elif re.search(r'(?i)(fatal|uncaught)', msg): kind = 'php_fatal'
                if not kind:
                    continue
                match = re.search(r'#APVH_([^:\]\s]+)', msg)
                site = match[1].lower() if match and match[1].lower() in domains else ''
                key = (kind, site)
                counts[key] += 1
                pid = re.search(r'(?:processing by PID:|\bpid[=: ]+)\s*(\d+)', msg, re.I)
                if pid:
                    examples[key] = int(pid[1])
            for (kind, site), count in counts.items():
                out.log('web_error_summary', error_type=kind, site=site, user=domains.get(site, ''),
                        count=count, sample_pid=examples.get((kind, site)),
                        coverage=quality, level='warning')
        except OSError:
            out.log('collector_unavailable', component='global_error_log', path=path_text, level='warning')
    state['error_files'] = positions


def collect_kernel(out, state):
    now = int(time.time())
    since = max(now - 120, int(state.get('journal_at', now - 60)))
    previous_cursor = state.get('journal_cursor')
    args = ['/usr/bin/journalctl', '--no-pager', '-k', '-o', 'json', '-n', '200', '--until', '@' + str(now)]
    args += ['--after-cursor', previous_cursor] if previous_cursor else ['--since', '@' + str(since)]
    raw = command(args, timeout=5)
    for line in raw.splitlines():
        record = json.loads(line)
        cursor = record.get('__CURSOR')
        if cursor == previous_cursor:
            continue
        message = str(record.get('MESSAGE', ''))
        kind = None
        if re.search(r'(?i)(oom-kill|out of memory|killed process)', message): kind = 'oom'
        elif re.search(r'(?i)(segfault|general protection fault)', message): kind = 'native_crash'
        elif re.search(r'(?i)(I/O error|blk_update_request|blocked for more than)', message): kind = 'disk_or_kernel_stall'
        if kind:
            out.log('kernel_event', error_type=kind, level='error')
        state['journal_cursor'] = cursor
    state['journal_at'] = now


def main():
    if os.geteuid() != 0:
        raise RuntimeError('collector_requires_root')
    profile = read_json(BASE / 'profile.json', {})
    lock = open(STATE / 'collector.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = read_json(STATE / 'state.json', {})
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if state.get('boot_id') != boot:
        state.pop('proc', None)
        state.pop('proc_uptime', None)
        state.pop('log_policy_v2', None)
    state['boot_id'] = boot
    out = Output(profile, state)
    started = time.monotonic()
    domains, accounts, users = inventory()
    components = {}
    for name, fn in [
        ('processes', lambda: collect_processes(out, state, domains, accounts, users)),
        ('lve', lambda: collect_lve(out, profile)),
        ('access', lambda: collect_access(out, state, domains, profile)),
        ('errors', lambda: collect_errors(out, state, domains, profile)),
        ('kernel', lambda: collect_kernel(out, state))]:
        try:
            components[name] = fn()
        except Exception as error:
            components[name] = 'failed'
            out.log('collector_error', component=name, error_class=type(error).__name__, level='warning')
    duration = time.monotonic() - started
    out.log('collector_heartbeat', duration_seconds=round(duration, 3), accounts=len(accounts),
            domains=len(domains), components=components, installation_id=profile['installation_id'],
            suppressed_summary_logs=dict(out.suppressed))
    out.metric('duplika_collector_duration_seconds', duration)
    out.metric('duplika_collector_heartbeat', 1)
    out.metric('duplika_collector_failed_components', sum(v == 'failed' for v in components.values()))
    out.flush()
    atomic_json(STATE / 'state.json', state)
    print(json.dumps({'status': 'ok', 'duration_seconds': round(duration, 3),
                      'components': components, 'logs': len(out.logs), 'metrics': len(out.metrics)}))


if __name__ == '__main__':
    main()
