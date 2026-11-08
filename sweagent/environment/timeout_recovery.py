"""Kill only a timed-out shell and its descendants inside its disposable container."""
import os
from pathlib import Path
import signal


def terminate_shell(pid_file):
    if not Path('/.dockerenv').exists():
        raise RuntimeError('Shell recovery is restricted to Docker containers')
    root = int(Path(pid_file).read_text())
    if root <= 1 or root == os.getpid():
        raise RuntimeError('Invalid recovery shell PID')
    comm = Path('/proc/{}/comm'.format(root))
    if not comm.exists():
        return
    if comm.read_text().strip() != 'bash':
        raise RuntimeError('Recovery PID is no longer the expected bash shell')
    children = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(path.parent.name))
        except (OSError, ValueError, IndexError):
            continue
    ordered, pending = [], [root]
    while pending:
        pid = pending.pop()
        ordered.append(pid)
        pending.extend(children.get(pid, []))
    for pid in reversed(ordered):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


if __name__ == '__main__':
    import sys
    terminate_shell(sys.argv[1])
