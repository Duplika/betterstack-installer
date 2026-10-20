#!/usr/bin/python3
"""Read/traverse only selected logs; record original ACLs, with inode identity."""
import glob
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

BASE = Path('/etc/duplika-betterstack')
STATE = BASE / 'acl-original.json'

def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout

def safe(path):
    return os.path.realpath(path) == path and os.path.exists(path)

def main():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if '--restore' in sys.argv:
        for path, old in state.items():
            if not safe(path):
                continue
            st = os.stat(path)
            if [st.st_dev, st.st_ino] != old['identity']:
                continue  # Do not restore obsolete permissions to rotated files.
            subprocess.run(['setfacl', '--restore=-'], input=old['acl'], text=True, check=True)
        return
    profile = json.loads((BASE / 'profile.json').read_text())
    targets = {}
    for pattern in profile['log_patterns']:
        # Traverse ancestors, and list ONLY the final directory of a glob.
        parent = Path(pattern).parent
        for directory in [parent] + list(parent.parents):
            p = str(directory)
            if p == '/':
                continue
            perm = 'r-x' if directory == parent and glob.has_magic(pattern) else '--x'
            if safe(p) and os.path.isdir(p):
                targets[p] = 'r-x' if targets.get(p) == 'r-x' else perm
        for path in glob.glob(pattern):
            if safe(path) and stat.S_ISREG(os.stat(path).st_mode):
                targets[path] = 'r--'
    for path, perm in sorted(targets.items(), key=lambda item: len(item[0])):
        tests = ['-r', '-x'] if perm == 'r-x' else ['-r' if perm == 'r--' else '-x']
        if all(subprocess.call(['runuser', '-u', 'vector', '--', 'test', test, path]) == 0 for test in tests):
            continue
        st = os.stat(path)
        identity = [st.st_dev, st.st_ino]
        if state.get(path, {}).get('identity') != identity:
            state[path] = {'identity': identity, 'acl': run(['getfacl', '-p', path])}
            temp = STATE.with_suffix('.tmp')
            temp.write_text(json.dumps(state))
            temp.chmod(0o600)
            temp.replace(STATE)
        # Re-check after saving baseline. No recursive ACL grants.
        if not safe(path) or [os.stat(path).st_dev, os.stat(path).st_ino] != identity:
            raise RuntimeError('La ruta cambió durante el ajuste de permisos: ' + path)
        run(['setfacl', '-m', 'u:vector:' + perm, path])

if __name__ == '__main__':
    main()
