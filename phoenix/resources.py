"""Cross-process I/O gates and optional historical adaptive admission."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import time

from phoenix.run_state import read


@contextmanager
def io_slot(output, name, maximum, deadline=None):
    output = Path(output)
    directory = output / "controls"
    directory.mkdir(parents=True, exist_ok=True)
    held = None
    field = {"start": "environment_start_concurrency", "commands": "local_command_concurrency"}[name]
    while held is None:
        if deadline and time.time() >= deadline:
            raise TimeoutError("Run I/O admission deadline reached")
        count = max(1, min(maximum, read(output / "resource_policy.json").get(field, maximum)))
        for i in range(count):
            stream = (directory / f"{name}-{i}.lock").open("a")
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = stream
                break
            except BlockingIOError:
                stream.close()
        if held is None:
            time.sleep(.25)
    try:
        yield
    finally:
        held.close()
