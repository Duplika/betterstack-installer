#!/usr/bin/env python3
# Duplika Vector profile v1: only grant traversal/read to the configured paths.
import glob, json, os, stat, subprocess, sys
p = json.load(open('/etc/vector/duplika-profile.json'))
root = p['docroot']
def can(mode, path):
    return subprocess.call(['runuser','-u','vector','--','test',mode,path]) == 0
def grant(path, perm, test):
    if os.path.islink(path):
        raise SystemExit('No se conceden permisos sobre symlinks: ' + path)
    if not can(test, path) and '--check' not in sys.argv:
        subprocess.check_call(['setfacl','-m','u:vector:' + perm, path])
# Paths must remain in the approved directory trees after resolving ancestors.
for path in ['/var/log/apache2', root, root + '/wp-content', root + '/wp-content/uploads', root + '/wp-content/uploads/wc-logs']:
    if os.path.realpath(path) != path:
        raise SystemExit('Ruta redirigida: ' + path)
    if os.path.isdir(path):
        grant(path, '--x', '-x')
files = ['/var/log/apache2/error_log', '/var/log/apache2/stderr.log', root + '/error_log']
# cPanel context is read by the separate root reader. Never change its ACL/flags.
files += glob.glob(root + '/wp-content/uploads/wc-logs/fatal-errors-*.log')
for path in files:
    if os.path.exists(path):
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise SystemExit('Archivo no regular: ' + path)
        grant(path, 'r--', '-r')
