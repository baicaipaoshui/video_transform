"""Fail-closed filesystem guard for an isolated conversion process and children.

Activated only when VIDEO_CONVERSION_ISOLATION_ROOT is set. The parent imports
this file explicitly; Python children load it through PYTHONPATH/sitecustomize.
"""
import os
import sys
import fcntl
from pathlib import Path


ROOT_ENV = 'VIDEO_CONVERSION_ISOLATION_ROOT'
_ROOT = Path(os.environ[ROOT_ENV]).resolve(strict=True) if os.environ.get(ROOT_ENV) else None


def check_write(path, *, dir_fd=None, root=None):
    bound = Path(root).resolve(strict=True) if root is not None else _ROOT
    if bound is None:
        raise PermissionError('Conversion isolation root is not configured')
    if dir_fd not in (None, -1):
        raise PermissionError('Unresolved descriptor-relative write is forbidden')
    if isinstance(path, int):
        try:
            if sys.platform == 'darwin':
                path = os.fsdecode(fcntl.fcntl(path, 50, bytes(1024)).split(b'\0', 1)[0])
            else:
                path = os.readlink('/proc/self/fd/' + str(path))
        except OSError as error:
            raise PermissionError('Unresolved writable file descriptor') from error
    path = Path(os.fsdecode(path))
    if '..' in path.parts:
        raise PermissionError('Parent traversal is forbidden: ' + str(path))
    destination = path.resolve(strict=False)
    if not destination.is_relative_to(bound):
        raise PermissionError('Write escapes conversion isolation root: ' + str(path))
    return destination


def _audit(event, args):
    if event == 'open':
        path, mode, flags = args
        writing = any(letter in (mode or '') for letter in 'wax+') or bool(flags & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND |
            getattr(os, 'O_TMPFILE', 0)))
        if writing:
            check_write(path)
    elif event in {'os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod',
                   'os.chown', 'os.utime', 'os.truncate'}:
        check_write(args[0], dir_fd=args[-1] if event != 'os.truncate' else None)
    elif event == 'os.rename':
        check_write(args[0], dir_fd=args[2])
        check_write(args[1], dir_fd=args[3])
    elif event in {'os.link', 'os.symlink'}:
        raise PermissionError('Links are forbidden in isolated conversion output')
    elif event in {'subprocess.Popen', 'os.exec'}:
        command = args[1]
        environment = args[3] if event == 'subprocess.Popen' else args[2]
        environment = environment if environment is not None else os.environ
        project = Path(__file__).resolve().parents[5]
        converter = project/'json-coverter/skills/case-conversion/scripts'
        report = project/'global-video-understanding/skills/case-delivery/scripts/report.py'
        script = Path(os.fsdecode(command[1])).resolve(strict=False) if isinstance(command, (list, tuple)) and len(command) > 1 else None
        allowed = (script == converter/'process.py' or script == report or
                   script is not None and script.name == 'convert.py' and
                   script.parent.parent == converter/'scenes')
        if (not isinstance(command, (list, tuple)) or len(command) < 2
                or not allowed
                or environment.get(ROOT_ENV) != str(_ROOT)
                or str(Path(__file__).resolve().parent) not in environment.get('PYTHONPATH', '').split(os.pathsep)):
            raise PermissionError('Only guarded deterministic Converter/report Python children are allowed')
    elif event in {'os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty'}:
        raise PermissionError('Unguarded process launch is forbidden')


if _ROOT is not None:
    sys.addaudithook(_audit)
