"""Read-only platform/capability probes. No package installation or repair."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

VERSION = '4.0.0-rc19'
SCHEMA = 1
MAX_OUTPUT = 8 * 1024 * 1024


class ProbeError(RuntimeError):
    pass


def trusted_executable(name):
    value = shutil.which(name, path='/usr/sbin:/usr/bin:/sbin:/bin')
    if not value:
        return None
    invoked = Path(value)
    path = invoked.resolve()
    for item in (path, *path.parents):
        st = item.stat()
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise ProbeError('unsafe_executable:' + name)
    # Multiplexers such as dnf-utils select a subcommand from argv[0].
    # Validate both routes, but invoke the original trusted command name.
    for item in (invoked, *invoked.parents):
        st = item.lstat()
        if st.st_uid != 0 or (not item.is_symlink() and st.st_mode & 0o022):
            raise ProbeError('unsafe_executable:' + name)
        target = item.stat()
        if target.st_uid != 0 or target.st_mode & 0o022:
            raise ProbeError('unsafe_executable:' + name)
    return str(invoked)


def run(args, timeout=20):
    try:
        # Redirect output to a bounded temporary file to avoid unbounded PIPE memory.
        import tempfile
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            child = subprocess.Popen(args, stdout=stdout, stderr=stderr,
                env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
            import time
            end = time.monotonic() + timeout
            try:
                while child.poll() is None:
                    if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > MAX_OUTPUT:
                        raise ProbeError('output_limit:' + Path(args[0]).name)
                    if time.monotonic() >= end:
                        raise ProbeError('timeout:' + Path(args[0]).name)
                    time.sleep(0.05)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
            stdout.seek(0); stderr.seek(0)
            out, err = stdout.read(MAX_OUTPUT + 1), stderr.read(MAX_OUTPUT + 1)
            if len(out) + len(err) > MAX_OUTPUT:
                raise ProbeError('output_limit:' + Path(args[0]).name)
            return subprocess.CompletedProcess(args, child.returncode,
                out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace'))
    except OSError:
        raise ProbeError('execution_failed:' + Path(args[0]).name) from None


def parse_json(result, stage):
    if result.returncode:
        raise ProbeError(stage + ':command_failed:exit=' + str(result.returncode))
    if not result.stdout.strip():
        raise ProbeError(stage + ':empty_stdout')
    try:
        return json.loads(result.stdout)
    except ValueError:
        # stdout/stderr can contain customer data: never echo them into telemetry.
        raise ProbeError(stage + ':invalid_json') from None


def detect(osrelease=Path('/etc/os-release'), runner=run, finder=trusted_executable):
    info = dict(line.split('=', 1) for line in osrelease.read_text().splitlines() if '=' in line)
    distro = info.get('ID', '').strip('"\'').lower()
    version = info.get('VERSION_ID', '').strip('"\'')
    allowed = {'almalinux': ('8', '9'), 'cloudlinux': ('8', '9')}
    if version.split('.')[0] not in allowed.get(distro, ()):
        raise ProbeError('unsupported_os:' + distro + ':' + version)
    result = {'os_id': distro, 'os_version': version, 'edition': None,
              'lve_status': 'not_applicable', 'cloudlinux_top': None,
              'isolates_supported': False, 'mysql_governor': 'unavailable'}
    if distro == 'almalinux':
        return result
    detector = finder('cldetect')
    if not detector:
        raise ProbeError('cloudlinux:edition_detector_missing')
    edition = runner([detector, '--detect-edition'])
    known = ('CloudLinux OS Solo', 'CloudLinux OS Admin', 'CloudLinux OS Shared', 'CloudLinux OS Shared Pro')
    if edition.returncode or edition.stdout.strip() not in known:
        raise ProbeError('cloudlinux:edition_unrecognized')
    result['edition'] = edition.stdout.strip()
    if result['edition'] == 'CloudLinux OS Solo':
        result['lve_status'] = 'not_supported'
        return result
    top = finder('cloudlinux-top')
    if not top:
        raise ProbeError('lve:tool_missing_expected_supported_edition')
    value = parse_json(runner([top, '--json', '--max=1']), 'lve')
    if not isinstance(value, dict) or value.get('result') != 'success' or not isinstance(value.get('users'), list):
        raise ProbeError('lve:invalid_schema')
    help_result = runner([top, '--help'])
    result.update(lve_status='available', cloudlinux_top=top,
        isolates_supported=help_result.returncode == 0 and '--domains' in help_result.stdout,
        mysql_governor=value.get('mySqlGov', 'unavailable'))
    return result


def validate_target(target):
    import ipaddress
    required = {'host', 'server_ip', 'source_id', 'ingesting_host'}
    if set(target) - required - {'wordpress'} or not required <= set(target):
        raise ProbeError('target:invalid_fields')
    host = target['host']
    if not isinstance(host, str) or len(host) > 253 or '.' not in host or any(
            not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in host.split('.')):
        raise ProbeError('target:invalid_hostname')
    if not isinstance(target['server_ip'], str):
        raise ProbeError('target:invalid_ip')
    ipaddress.ip_address(target['server_ip'])
    sid = target['source_id']
    if type(sid) is not int or sid <= 0 or not re.fullmatch(
            's' + str(sid) + r'\.[a-z0-9-]+\.betterstackdata\.com', target['ingesting_host']):
        raise ProbeError('target:invalid_source')
    sites = target.get('wordpress', [])
    if not isinstance(sites, list) or len(sites) > 10:
        raise ProbeError('wordpress:limit_10_sites')
    for site in sites:
        if set(site) != {'user', 'domain', 'docroot', 'expires_at'}:
            raise ProbeError('wordpress:invalid_fields')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', site['user']):
            raise ProbeError('wordpress:invalid_user')
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]+', site['domain']):
            raise ProbeError('wordpress:invalid_domain')
        if not isinstance(site['docroot'], str) or not re.fullmatch(r'/home/' + re.escape(site['user']) + r'/[A-Za-z0-9_./-]+', site['docroot']):
            raise ProbeError('wordpress:unsafe_docroot_characters')
        root = Path(site['docroot'])
        if not root.is_absolute() or '..' in root.parts or not root.is_relative_to(Path('/home') / site['user']):
            raise ProbeError('wordpress:invalid_docroot')
        import datetime
        expiry = datetime.datetime.fromisoformat(site['expires_at'])
        if expiry.tzinfo is None:
            raise ProbeError('wordpress:expiry_requires_timezone')
    return target
