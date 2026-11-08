"""Shared admission pacing; never retries an API call or changes its payload."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time


def state_path(account, model, directory=None):
    root = Path(directory or f'/tmp/phoenix-api-pacing-{os.getuid()}')
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((account + '\0' + model.removeprefix('openai/')).encode()).hexdigest()
    return root / (key + '.json')


def wait_for_request(account, model, interval, *, deadline=None, directory=None):
    if not interval:
        return
    path = state_path(account, model, directory)
    while True:
        with path.open('a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            state = json.loads(handle.read() or '{}')
            now = time.time()
            delay = max(0, state.get('next_request_at', 0) - now)
            if deadline is not None and time.monotonic() + delay >= deadline:
                raise TimeoutError('Task walltime exhausted while waiting for API admission')
            if delay <= 0:
                first = state.setdefault('warmup_started_at', now)
                # Gradually ramp a new account/model pair: 8s -> 4s -> 2s.
                spacing = max(interval, 8 / (2 ** min(2, int(max(0, now - first) // 60))))
                state.update(next_request_at=now + spacing, last_request_at=now, interval=spacing)
                handle.seek(0); handle.truncate(); json.dump(state, handle); handle.flush()
                return
        time.sleep(min(delay, 1))


def cooldown(account, model, seconds=120, *, directory=None):
    path = state_path(account, model, directory)
    with path.open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        state = json.loads(handle.read() or '{}')
        until = time.time() + seconds
        state.update(next_request_at=max(state.get('next_request_at', 0), until),
                     warmup_started_at=until, cooldown_until=until)
        handle.seek(0); handle.truncate(); json.dump(state, handle); handle.flush()
