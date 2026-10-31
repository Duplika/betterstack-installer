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


# Daily, read-only LiteSpeed maintenance. No vendor shell scripts are executed.
# Keep this in the existing integrity-checked runtime so old transports do not change.
def numeric_version(value):
    import re
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,6}(?:\.[0-9]{1,6}){1,3}', value):
        raise ValueError('invalid_version')
    return tuple(map(int, value.split('.'))) + (0,) * (4 - len(value.split('.')))


def maintenance_text(path, reader, limit=16384):
    value = reader(Path(path))
    if len(value) > limit:
        raise ValueError('metadata_too_large')
    return value.decode('ascii').strip()


def catalog_text(url):
    """Fixed HTTPS origins, no redirects/proxies/curlrc, bounded body/time."""
    allowed = ('https://update.litespeedtech.com/ws/latest.php',
               'https://pecl.php.net/rest/r/timezonedb/stable.txt')
    if url not in allowed:
        raise ValueError('catalog_not_allowed')
    curl = trusted_executable('curl')
    if not curl:
        raise RuntimeError('curl_unavailable')
    result = run([curl, '-q', '--fail', '--silent', '--show-error',
        '--proto', '=https', '--tlsv1.2', '--connect-timeout', '3', '--max-time', '6',
        '--max-filesize', '16384', '--noproxy', '*', url], timeout=8)
    if result.returncode or result.stderr.strip() or len(result.stdout) > 16384:
        raise RuntimeError('catalog_failed')
    return result.stdout.strip()


def litespeed_home(reader):
    import re
    config = Path('/usr/local/cpanel/whostmgr/docroot/cgi/lsws/LSWS_HOME.config')
    home = Path('/usr/local/lsws')
    if config.exists() or config.is_symlink():
        # Unlike the vendor updater, do not silently fall back after bad config.
        text = maintenance_text(config, reader)
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')]
        if not lines or not lines[0].startswith('LSWS_HOME='):
            raise ValueError('home_config_invalid')
        value = lines[0].split('=', 1)[1]
        if not re.fullmatch(r'/[A-Za-z0-9/._-]+', value) or '..' in value:
            raise ValueError('home_config_invalid')
        home = Path(value)
    # No runtime process scan, binaries, network, PHP or catalogs on absent hosts.
    if not (home / 'bin/lshttpd').exists():
        return None
    if not trusted_executable(str(home / 'bin/lshttpd')):
        raise ValueError('binary_untrusted')
    return home


def timezone_observations(reader, fetch=catalog_text, finder=trusted_executable,
                          runner=run, php_root=Path('/opt/cpanel')):
    """Probe only EA4 runtimes with -n; no site/config code is executed."""
    import re
    roots = sorted(php_root.glob('ea-php[0-9][0-9]'))
    roots = [p for p in roots if (p / 'root/usr/bin/php').exists()]
    if len(roots) > 12:
        return {'status': 'unknown', 'reason': 'php_limit', 'php': []}
    if not roots:
        return {'status': 'not_applicable', 'php': []}
    try:
        latest = fetch('https://pecl.php.net/rest/r/timezonedb/stable.txt')
        numeric_version(latest)
    except Exception:
        latest = None
    rows = []
    for root in roots:
        row = {'php': root.name[6:], 'status': 'unknown'}
        rows.append(row)
        try:
            base = root / 'root'
            module = base / 'usr/lib64/php/modules/timezonedb.so'
            if not module.exists():
                row.update(status='missing', reason='extension_missing')
                continue
            # Validate ownership of binary/module and every resolved parent.
            path = module.resolve()
            for item in (module, *module.parents, path, *path.parents):
                st = item.stat()
                if st.st_uid != 0 or st.st_mode & 0o022:
                    raise ValueError('untrusted_module')
            if not path.is_file():
                raise ValueError('untrusted_module')
            ini_paths = [base / 'etc/php.ini', *sorted((base / 'etc/php.d').glob('*.ini'))]
            if len(ini_paths) > 128:
                raise ValueError('ini_limit')
            enabled = False
            for ini in ini_paths:
                if not ini.exists():
                    continue
                text = maintenance_text(ini, reader, limit=262144)
                for line in text.splitlines():
                    match = re.fullmatch(r'\s*extension\s*=\s*[\"\']?([^\"\';\s]+)[\"\']?\s*(?:;.*)?', line, re.I)
                    if match and match[1] in ('timezonedb', 'timezonedb.so', str(module)):
                        enabled = True
            if not enabled:
                row.update(status='missing', reason='extension_not_enabled')
                continue
            php = finder(str(base / 'usr/bin/php'))
            if not php:
                raise ValueError('php_untrusted')
            response = runner([php, '-n', '-d', 'extension=' + str(module), '-r',
                'echo extension_loaded("timezonedb") ? timezone_version_get() : "";'], timeout=3)
            installed = response.stdout.strip()
            if response.returncode or response.stderr.strip():
                raise ValueError('php_probe_failed')
            numeric_version(installed)
            row['installed'] = installed
            if latest is None:
                row['reason'] = 'catalog_failed'
            else:
                row.update(available=latest, status='update_available' if
                    numeric_version(installed) < numeric_version(latest) else 'current')
        except Exception:
            row.update(status='unknown', reason='local_probe_failed')
    statuses = {row['status'] for row in rows}
    status = next((s for s in ('missing', 'update_available', 'unknown') if s in statuses), 'current')
    return {'status': status, 'php': rows, **({'available': latest} if latest else {})}


def probe_litespeed(reader, fetch=catalog_text):
    import re
    result = {'status': 'unknown', 'server': {'status': 'unknown'},
              'timezonedb': {'status': 'unknown', 'php': []}}
    try:
        home = litespeed_home(reader)
        if home is None:
            return {'status': 'not_applicable', 'server': {'status': 'not_applicable'},
                    'timezonedb': {'status': 'not_applicable', 'php': []}}
        result['status'] = 'installed'
        installed = maintenance_text(home / 'VERSION', reader)
        numeric_version(installed)
        # lsup uses BUILD.<VERSION>; fall back to documented BUILD only if absent.
        build_path = home / ('BUILD.' + installed)
        build = maintenance_text(build_path if build_path.exists() else home / 'BUILD', reader)
        if not re.fullmatch(r'[0-9]{1,8}', build):
            raise ValueError('invalid_build')
        release = maintenance_text(home / 'autoupdate/release', reader)
        if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}-ent', release):
            result['reason'] = 'enterprise_not_confirmed'
            return result
        result['server'].update(installed=installed, installed_build=int(build))
    except Exception:
        result['reason'] = 'installation_probe_failed'
        return result
    try:
        catalog = fetch('https://update.litespeedtech.com/ws/latest.php')
        matches = re.findall(r'^LSWS_STABLE=([0-9]+(?:\.[0-9]+){1,3}) BUILD ([0-9]{1,8})\s*$', catalog, re.M)
        if len(matches) != 1:
            raise ValueError('invalid_stable_catalog')
        version, build = matches[0]
        remote, local = numeric_version(version), numeric_version(installed)
        state = 'version_available' if remote > local else (
            'build_available' if remote == local and int(build) > result['server']['installed_build'] else (
            'ahead_of_stable' if remote < local or int(build) < result['server']['installed_build'] else 'current'))
        result['server'].update(status=state, available=version, available_build=int(build), channel='stable')
    except Exception:
        result['server']['reason'] = 'catalog_failed'
    result['timezonedb'] = timezone_observations(reader, fetch)
    return result


def daily_litespeed(state, reader, writer, now, online=False):
    """Single daily attempt, errors included. Verification never triggers network."""
    import json
    import math
    path = Path(state) / 'litespeed-maintenance.json'
    old = {}
    try:
        old = json.loads(reader(path))
        checked = old.get('checked_at')
        valid = (isinstance(checked, (int, float)) and not isinstance(checked, bool)
                 and math.isfinite(checked) and isinstance(old.get('observation'), dict))
        if valid and 0 <= now - checked < 86400:
            return old
    except Exception:
        pass
    if not online:
        return {'checked_at': None, 'observation': {'status': 'not_checked'}}
    observed = probe_litespeed(reader)
    result = {'checked_at': now, 'observation': observed}
    writer(path, result)
    return result


def maintenance_metrics(record, target):
    import datetime
    observation = record['observation']
    if record.get('checked_at') is None:
        return []
    at = datetime.datetime.fromtimestamp(record['checked_at'], datetime.timezone.utc).isoformat()
    common = {'host': target['host'], 'server_ip': target['server_ip']}
    rows = []
    def add(name, state, tags=None):
        codes = {'not_applicable': -2, 'not_checked': -1, 'unknown': -1, 'current': 0,
                 'ahead_of_stable': 0, 'version_available': 1, 'build_available': 1,
                 'update_available': 1, 'missing': 2}
        rows.append({'dt': at, 'name': name, 'gauge': {'value': codes.get(state, -1)},
                     'tags': dict(common, status=state, **(tags or {}))})
    server = observation.get('server', {'status': 'unknown'})
    add('duplika_litespeed_update_state', server['status'], {
        key: str(server.get(key, '')) for key in ('installed', 'installed_build', 'available', 'available_build')})
    tz = observation.get('timezonedb', {'status': 'unknown', 'php': []})
    add('duplika_timezonedb_update_state', tz['status'])
    for php in tz.get('php', []):
        add('duplika_timezonedb_php_update_state', php['status'], {
            key: str(php.get(key, '')) for key in ('php', 'installed', 'available')})
    return rows
