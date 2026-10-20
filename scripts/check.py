#!/usr/bin/env python3
"""Offline tests, privacy guard and reproducible-artifact verification."""
import ast
import base64
import hashlib
import importlib.util
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '__pycache__', 'local', 'private', 'out', 'build'}


def inspect(name, data):
    text = data.decode('utf-8')
    if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', text):
        raise ValueError('Potential secret in ' + name)
    for match in re.findall(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])', text):
        try:
            ip = ipaddress.ip_address(match)
        except ValueError:
            continue
        allowed = any(ip in ipaddress.ip_network(network) for network in ('192.0.2.0/24','198.51.100.0/24','203.0.113.0/24','127.0.0.0/8')) or str(ip)=='0.0.0.0'
        if not allowed:
            raise ValueError('Non-documentation IP literal in ' + name)
    if name.endswith('.py'):
        tree = ast.parse(text,feature_version=(3,9))
        for node in ast.walk(tree):
            if isinstance(node,ast.Constant) and isinstance(node.value,str):
                value=node.value
                if re.fullmatch(r'[A-Za-z0-9_-]{20,}',value) and re.search('[a-z]',value) and re.search('[A-Z]',value) and not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}',value):
                    raise ValueError('Opaque literal requires private review: '+name)
    if name.endswith('.json'):
        def walk(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key.lower() in ('token','password','api_key','secret','access_token') and isinstance(item,str) and item not in ('TEST_SECRET','TEST_ONLY','ROTATED_SECRET'):
                        raise ValueError('Secret field in '+name)
                    walk(item)
            elif isinstance(value,list):
                for item in value:walk(item)
        walk(json.loads(text))


def main():
    paths=[p for p in ROOT.rglob('*') if p.is_file() and not set(p.relative_to(ROOT).parts)&SKIP]
    for p in paths:
        if p.is_symlink():raise ValueError('Symlink in public tree')
        inspect(str(p.relative_to(ROOT)),p.read_bytes())
    subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=ROOT,check=True,
        env=dict(os.environ,PYTHONPATH=str(ROOT/'src/agent'),PYTHONDONTWRITEBYTECODE='1'))
    spec=importlib.util.spec_from_file_location('build',ROOT/'scripts/build.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    with tempfile.TemporaryDirectory() as d:
        a,b=Path(d)/'a',Path(d)/'b'
        first,second=builder.build(a),builder.build(b)
        assert first==second,'Build is not reproducible'
        subprocess.run(['bash','-n',str(a/'installer.sh')],check=True)
        payload=(a/'installer.sh').read_text().split("<<'DUPLIKA_4_PAYLOAD'\n",1)[1].split('DUPLIKA_4_PAYLOAD',1)[0]
        raw=base64.b64decode(payload)
        assert hashlib.sha256(raw).hexdigest()==first['payload_sha256']
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as tar:
            assert sorted(tar.getnames())==sorted(first['files'])
            for entry in tar:
                assert entry.isfile() and '/' not in entry.name
                data=tar.extractfile(entry).read()
                assert hashlib.sha256(data).hexdigest()==first['files'][entry.name]
                inspect(entry.name,data)
        released=ROOT/'releases'/builder.version()
        if released.exists():
            for name in ('installer.sh','manifest.json','SHA256SUMS'):
                assert (released/name).read_bytes()==(a/name).read_bytes(),'Release differs from sources: '+name
    print('Public privacy guard, Python 3.9 syntax, deterministic payload and Bash syntax: OK')
    print('No remote install, service changes or native Vector execution were performed.')


if __name__=='__main__':main()
