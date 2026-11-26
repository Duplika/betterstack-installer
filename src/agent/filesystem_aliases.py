"""Coalesce matching ext4 root capacity samples on reviewed host-only graphs."""
import copy
import json
import os
from pathlib import Path

CAPACITY = ['filesystem_' + field for field in ('total_bytes', 'free_bytes', 'used_bytes',
    'used_ratio', 'inodes_total', 'inodes_free', 'inodes_used', 'inodes_used_ratio')]
# Limit discovery to system mountpoints. Never publish arbitrary tenant paths.
from capabilities import SYSTEM_MOUNTS as SYSTEM_PATHS


def discover(pid):
    mounts = {}
    proc = Path('/proc') / str(int(pid))
    with (proc / 'mountinfo').open() as rows:
        for row in rows:
            left, _, right = row.partition(' - ')
            fields, fs = left.split(), right.split()
            if len(fields) >= 6 and len(fs) >= 3 and fields[4] in SYSTEM_PATHS:
                mounts[fields[4]] = (fields[2], fs[0], fs[1])
    root = mounts.get('/')
    if not root or root[1] != 'ext4':
        return []
    def size(path):
        path = proc / 'root' / path.lstrip('/')
        info = os.statvfs(path)
        return os.stat(path).st_dev, info.f_frsize, info.f_blocks, info.f_files
    aliases = []
    reference = size('/')
    for path in SYSTEM_PATHS[1:]:
        if mounts.get(path) == root:
            try:
                if size(path) == reference:
                    aliases.append(path)
            except OSError:
                continue
    return aliases


def prepare(original, aliases, host, live_flags=True):
    if not isinstance(aliases, list) or any(path not in SYSTEM_PATHS[1:] for path in aliases):
        raise RuntimeError('filesystem:invalid_aliases')
    cfg = copy.deepcopy(original)
    if not aliases:
        return cfg
    # Compare live samples from the same scrape. A remounted path with a new
    # device, value or filesystem, or a group missing its root, passes through.
    paths = json.dumps(['/', *aliases])
    transforms = cfg['transforms']
    route = 'filesystem_route'
    transforms[route] = {'type': 'route', 'inputs': ['legacy_host_essential'],
        'route': {'capacity': 'includes(' + json.dumps(CAPACITY) + ', .name) && .tags.filesystem == "ext4" && includes(' + paths + ', .tags.mountpoint) && exists(.dt) && exists(.tags.device) && exists(.gauge.value)'}}
    transforms['filesystem_snapshot'] = {'type': 'remap', 'inputs': [route + '.capacity'],
        'source': 't = parse_timestamp!(to_string!(.dt), format: "%+")\n.row = encode_json(.)\n.bucket = floor(to_unix_timestamp(t) / 5)'}
    transforms['filesystem_collect'] = {'type': 'reduce', 'inputs': ['filesystem_snapshot'],
        'group_by': ['bucket', 'name', 'tags.host', 'tags.device', 'tags.filesystem', 'gauge.value'],
        'expire_after_ms': 2000, 'flush_period_ms': 500, 'merge_strategies': {'row': 'array'}}
    transforms['filesystem_emit'] = {'type': 'remap', 'inputs': ['filesystem_collect'],
        'source': 'rows = []\nroot = []\nfor_each(array!(.row)) -> |_i, encoded| {\n row = object!(parse_json!(string!(encoded)))\n rows = push(rows,row)\n if row.tags.mountpoint == "/" { root = push(root,row) }\n}\nif length(root) == 1 { . = root } else { . = rows }'}
    transforms['base_cpu_route']['inputs'] = [route + '._unmatched', 'filesystem_emit']
    # Report read-only independently for every selected mount, including aliases.
    # awk reads only kernel mount metadata. No credential is passed to the child.
    from capabilities import mount_flags_source
    if not live_flags:
        return cfg
    cfg['sources']['mount_flags'] = mount_flags_source()
    transforms['mount_readonly'] = {'type':'remap','inputs':['mount_flags'],
        'source': 'p = split!(.message, " ")\n. = {"dt": now(), "name": "duplika_storage_volume_readonly", "gauge": {"value": to_int!(p[1])}, "tags": {"host": ' + json.dumps(host) + ', "mountpoint": p[0], "volume": p[0]}}'}
    cfg['sinks']['duplika_metrics']['inputs'].append('mount_readonly')
    return cfg
