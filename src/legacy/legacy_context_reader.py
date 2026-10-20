#!/usr/bin/env python3
'''Read only selected root logs; write a bounded, private JSON relay. Python 3.6+.'''
import argparse
import datetime
import fcntl
import json
import os
import pathlib
import pwd
import re
import stat
import time

FILES = (
    '/var/log/chkservd.log',
    '/usr/local/cpanel/logs/safeapacherestart_log',
    '/usr/local/cpanel/logs/queueprocd.log',
    '/usr/local/cpanel/logs/tailwatchd_log',
    '/usr/local/cpanel/logs/error_log',
    '/var/log/dnf.rpm.log',
)
DIRECTORY = pathlib.Path('/var/log/duplika-vector-context')
INITIAL_BYTES = 4 * 1024 * 1024
READ_BYTES = 2 * 1024 * 1024
MAX_LINE = 64 * 1024
ROTATE_BYTES = 8 * 1024 * 1024
KEEP = 4


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def open_source(path):
    if os.path.realpath(path) != path:
        raise RuntimeError('Ruta redirigida: ' + path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise RuntimeError('Log no regular: ' + path)
    return os.fdopen(fd, 'rb')


def selected(path, text):
    if path.endswith('/safeapacherestart_log'):
        return True
    if path.endswith('/dnf.rpm.log'):
        return re.search(r'ea-php|litespeed|lsphp|imunify|monarx|glibc|mariadb', text, re.I) is not None
    if path.endswith('/chkservd.log'):
        return re.search(r'httpd|apache|litespeed|lsws|restartsrv', text, re.I) is not None
    return re.search(r'restartsrv_(httpd|apache|lsws)|safeapacherestart|rebuildhttpdconf|'
                     r'(restart|reload|graceful|stop|start|fail|kill).*?(httpd|apache|litespeed|lsws)|'
                     r'(httpd|apache|litespeed|lsws).*?(restart|reload|graceful|stop|start|fail|kill)', text, re.I) is not None


def scrub(text):
    text = re.sub(r'''(?ix)(?:access_token|api[_-]?(?:key|secret)|token|secret|password|passwd|authorization|cookie|session)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)''', '[CREDENCIAL_OCULTA]', text)
    text = re.sub(r'(?i)Bearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [OCULTO]', text)
    text = re.sub(r'(?i)https?://[^\s<>]+', '[URL_OCULTA]', text)
    text = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[EMAIL_OCULTO]', text)
    return re.sub(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', '[IP_OCULTA]', text)


def read_batch(path, previous, host):
    '''No writes to the source, including no chmod, chown, setfacl or chattr.'''
    records = []
    with open_source(path) as stream:
        st = os.fstat(stream.fileno())
        same = previous.get('inode') == st.st_ino and previous.get('device') == st.st_dev
        saved = int(previous.get('offset', 0))
        reset = not same or saved > st.st_size
        start = max(0, st.st_size - INITIAL_BYTES) if reset else saved
        dropping = bool(previous.get('dropping')) if not reset else start > 0
        if reset and start > 0:
            stream.seek(start - 1)
            dropping = stream.read(1) != b'\n'
        stream.seek(start)
        oversize = 0
        while stream.tell() - start < READ_BYTES:
            offset = stream.tell()
            line = stream.readline(MAX_LINE + 1)
            if not line:
                break
            complete = line.endswith(b'\n')
            if dropping:
                dropping = not complete
                continue
            if len(line) > MAX_LINE:
                oversize += 1
                dropping = not complete
                continue
            if not complete:
                stream.seek(offset)  # Retry a partially written line on the next run.
                break
            text = line.decode('utf-8', errors='replace').rstrip('\r\n')
            if selected(path, text):
                records.append({'message': scrub(text), 'file': path, 'file_offset': offset,
                                'source_inode': st.st_ino, 'source_device': st.st_dev,
                                'host': host, 'context_reader': True})
        current = {'inode': st.st_ino, 'device': st.st_dev, 'offset': stream.tell(), 'dropping': dropping, 'status': 'ok'}
        if reset or previous.get('status') != 'ok' or oversize:
            records.insert(0, {'message': utcnow() + ' Context reader source status', 'file': path,
                              'host': host, 'event_kind': 'context_reader_status', 'reader_status': 'ok',
                              'read_from_offset': start, 'source_size': st.st_size,
                              'initial_bytes_skipped': start if reset else 0,
                              'source_reset': reset, 'oversize_lines_skipped': oversize})
    return records, current


def private_file(path, flags, mode):
    fd = os.open(str(path), flags | os.O_NOFOLLOW, mode)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1:
        os.close(fd)
        raise RuntimeError('Archivo privado inseguro: ' + str(path))
    os.fchmod(fd, mode)
    return fd


def append_records(directory, records, gid):
    target = directory / 'context.jsonl'
    out = None
    size = target.stat().st_size if target.exists() else 0
    try:
        for record in records:
            data = (json.dumps(record, ensure_ascii=True) + '\n').encode()
            if size + len(data) > ROTATE_BYTES:
                if out is not None:
                    out.flush()
                    os.fsync(out.fileno())
                    out.close()
                    out = None
                for index in range(KEEP, 0, -1):
                    src = target if index == 1 else directory / ('context.%d.jsonl' % (index - 1))
                    dst = directory / ('context.%d.jsonl' % index)
                    if src.exists():
                        os.replace(str(src), str(dst))
                size = 0
            if out is None:
                fd = private_file(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
                out = os.fdopen(fd, 'ab')
                os.fchown(fd, os.geteuid(), gid)
            out.write(data)
            size += len(data)
    finally:
        if out is not None:
            out.flush()
            os.fsync(out.fileno())
            out.close()


def check_sources():
    for path in FILES:
        try:
            with open_source(path):
                print('LECTURA OK: ' + path)
        except FileNotFoundError:
            print('AUSENTE (opcional): ' + path)


def collect(directory, host, gid):
    if os.path.realpath(str(directory)) != str(directory):
        raise RuntimeError('Directorio de salida redirigido')
    st = directory.stat()
    if st.st_uid != os.geteuid() or st.st_mode & 0o022:
        raise RuntimeError('Directorio de salida no privado')
    lock = private_file(directory / '.lock', os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(lock, 'r+') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        state_path = directory / 'state.json'
        if state_path.exists():
            with os.fdopen(private_file(state_path, os.O_RDONLY, 0o600)) as src:
                state = json.load(src)
        else:
            state = {}
        for path in FILES:
            previous = state.get(path, {})
            try:
                records, current = read_batch(path, previous, host)
            except (OSError, RuntimeError) as exc:
                status = 'missing' if isinstance(exc, FileNotFoundError) else 'unreadable'
                current = dict(previous, status=status)
                records = [] if previous.get('status') == status else [
                    {'message': utcnow() + ' Context reader source status', 'file': path, 'host': host,
                     'event_kind': 'context_reader_status', 'reader_status': status,
                     'error_type': type(exc).__name__, 'error_errno': getattr(exc, 'errno', None)}]
            append_records(directory, records, gid)
            state[path] = current
        if time.time() - state.get('_heartbeat', 0) >= 600:
            append_records(directory, [{'message': utcnow() + ' Context reader heartbeat', 'host': host,
                                        'event_kind': 'context_reader_heartbeat'}], gid)
            state['_heartbeat'] = time.time()
        fd = private_file(directory / 'state.new', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(state, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(str(directory / 'state.new'), str(state_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        check_sources()
        return
    with open('/etc/vector/duplika-profile.json') as src:
        profile = json.load(src)
    if tuple(profile.get('context_files', [])) != FILES:
        raise RuntimeError('Lista de logs distinta de la autorizada')
    collect(DIRECTORY, profile['host'], pwd.getpwnam('vector').pw_gid)


if __name__ == '__main__':
    main()
