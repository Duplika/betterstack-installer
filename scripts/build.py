#!/usr/bin/env python3
"""Deterministic, allowlisted build. No runtime configuration is included."""
import argparse
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
AGENT = ('capabilities.py', 'collector.py', 'config.py', 'health.py', 'identity.py', 'install_base.py', 'installer.py', 'upgrade_rc3.json', 'upgrade_rc4.json', 'upgrade_rc5.json')
LEGACY = ('legacy_identity.py', 'legacy_cloud_config.py', 'legacy_cloud_collector.py',
          'legacy_alma_profile.py', 'legacy_alma_acl.py', 'legacy_context_reference.py',
          'legacy_context_acl.py', 'legacy_context_reader.py')


def version():
    value = re.search(r"^VERSION = '([^']+)'$", (ROOT/'src/agent/capabilities.py').read_text(), re.M)[1]
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-rc[0-9]+)?', value):
        raise ValueError('Invalid release version')
    return value


def build(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory/'installer.sh'
    if any((directory/name).exists() for name in ('installer.sh','manifest.json','SHA256SUMS')):
        raise ValueError('Build output is immutable; choose an empty directory')
    files = {name:(ROOT/'src/agent'/name).read_bytes() for name in AGENT}
    files.update({name:(ROOT/'src/legacy'/name).read_bytes() for name in LEGACY})
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o600, 0
            archive.addfile(info, io.BytesIO(data))
    payload = gzip.compress(stream.getvalue(), mtime=0)
    # RFC 1952 OS byte varies across Python releases; canonicalize to unknown.
    payload = payload[:9] + bytes([255]) + payload[10:]
    payload_sha = hashlib.sha256(payload).hexdigest()
    script = '''#!/bin/bash
# Duplika Better Stack __VERSION__. Public code; no credentials embedded.
set +x
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PYTHONDONTWRITEBYTECODE=1
[[ $EUID -eq 0 ]] || { echo 'Ejecutar como root.' >&2; exit 1; }
[[ -x /usr/bin/python3 ]] || { echo 'Se requiere Python 3.9 o posterior.' >&2; exit 1; }
bs4_work=$(mktemp -d /root/duplika-release4.XXXXXX)
trap 'rm -rf -- "$bs4_work"' EXIT
base64 --decode > "$bs4_work/payload.tgz" <<'DUPLIKA_4_PAYLOAD'
__PAYLOAD__DUPLIKA_4_PAYLOAD
printf '%s  %s\\n' '__PAYLOAD_SHA__' "$bs4_work/payload.tgz" | sha256sum --check --status
tar -xzf "$bs4_work/payload.tgz" -C "$bs4_work"
rm "$bs4_work/payload.tgz"
/usr/bin/python3 "$bs4_work/installer.py" "$@"
'''.replace('__VERSION__',version()).replace('__PAYLOAD__',base64.encodebytes(payload).decode()).replace('__PAYLOAD_SHA__',payload_sha)
    output.write_text(script); output.chmod(0o600)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest = {'version':version(),'channel':'candidate' if '-rc' in version() else 'stable',
        'contains_credentials':False,'remote_verified':False,'installer_sha256':digest,
        'payload_sha256':payload_sha,'files':{name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())}}
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (directory/'SHA256SUMS').write_text(digest+'  installer.sh\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    print(json.dumps(build(parser.parse_args().output),indent=2))
