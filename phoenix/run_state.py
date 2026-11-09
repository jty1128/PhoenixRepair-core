"""Durable run identities, bounded retry accounting and owned process control."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import time

from phoenix.settings import fingerprint, write_json


def read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else ({} if default is None else default)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextmanager
def lock(path, *, blocking=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def bind(path, value):
    """Refuse to reuse paid work with changed inputs, models or method settings."""
    previous = read(path)
    identity = fingerprint(value)
    if previous and previous.get("sha256") != identity:
        raise ValueError("Recovery input/configuration mismatch: " + str(path))
    if not previous:
        write_json(Path(path), {"sha256": identity, "value": value})
    return identity


def process_identity(pid):
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
        return {"state": fields[0], "start_ticks": fields[19], "pgid": int(fields[2])}
    except (FileNotFoundError, ProcessLookupError):
        return None


def owned_alive(record):
    ident = process_identity(record["pid"])
    if not ident or ident["state"] == "Z":
        return False
    if ident["start_ticks"] != record["start_ticks"] or ident["pgid"] != record["pid"]:
        raise RuntimeError("Worker PID identity changed; refusing to adopt or signal it")
    return True


def stop_owned(record, grace=10):
    """Never signal a reused PID or a shared shell process group."""
    if not owned_alive(record):
        return
    pid = record["pid"]
    # Hold pidfds while signalling: neither an exited group leader nor one of
    # its children can turn into an unrelated reused PID between checks.
    handles = []
    for path in Path('/proc').glob('[0-9]*'):
        child = int(path.name)
        try:
            handle = os.pidfd_open(child)
            ident = process_identity(child)
            if ident and ident['pgid'] == pid:
                handles.append(handle)
            else:
                os.close(handle)
        except (ProcessLookupError, PermissionError):
            continue
    for handle in handles:
        try:
            signal.pidfd_send_signal(handle, signal.SIGTERM)
        except ProcessLookupError:
            pass
    until = time.monotonic() + grace
    while owned_alive(record) and time.monotonic() < until:
        time.sleep(.1)
    for handle in handles:
        try:
            signal.pidfd_send_signal(handle, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            os.close(handle)


def find_worker(request_path):
    """Close the controller-crash window between Popen and writing process.json."""
    expected = str(Path(request_path).resolve())
    found = []
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if cmdline.stat().st_uid != os.getuid():
                continue
            args = cmdline.read_bytes().decode().split("\0")
            if "phoenix.worker" not in args or expected not in args:
                continue
            pid = int(cmdline.parent.name)
            ident = process_identity(pid)
            if ident and ident["pgid"] == pid and ident["state"] != "Z":
                found.append({"pid": pid, "start_ticks": ident["start_ticks"]})
        except (OSError, UnicodeError, ValueError):
            continue
    if len(found) > 1:
        raise RuntimeError("Multiple workers own the same attempt; refusing duplicate work")
    return found[0] if found else None


def verified_result(stage):
    stage = Path(stage)
    result = read(stage / "result.json")
    if result.get("status") != "completed":
        return None
    patch = stage / "final.patch"
    if not patch.is_file() or digest(patch) != result.get("patch_sha256"):
        raise ValueError("Completed stage patch changed: " + str(stage))
    return {**result, "patch": str(patch)}


def retry_reason(log, exit_status, *, model_started, trajectory=(), api_error=None):
    """Historical infrastructure categories; model/format failures are terminal."""
    if exit_status == "exit_api":
        # Prefer structured model errors over strings in tool output. A timeout
        # printed by a repository test is not an API transport failure.
        return ("api_transport_failure", 1) if (api_error or {}).get("retryable") else ("api_permanent_failure", 0)
    if not model_started:
        if "posix_fallocate64: symbol not found" in log or "/opt/phoenix-python/bin/python3: not found" in log:
            return "portable_runtime_unavailable", 1
        if "No such image" in log or "image_not_ready" in log:
            return "image_not_ready", 3
        if any(x in log for x in ("Runtime startup timed out", "Startup timed out", "CommandTimeoutError",
                                  "TIMEOUT", "TimeoutError", "Connection refused", "Address already in use")):
            return "container_start_timeout", 3
    if "BlockingIOError" in log and "Resource temporarily unavailable" in log:
        return "container_pid_exhaustion", 1
    if ("Runtime is no longer alive" in log or "RuntimeError: Runtime not started" in log
            or exit_status == "exit_environment_error"):
        return "terminal_session_failure", 1
    if exit_status == "exit_submission_timeout":
        return "submission_timeout", 1
    tail = trajectory[-2:]
    if (exit_status in ('exit_action_loop', 'exit_command_timeout') and len(tail) == 2
            and all(s.get('action', '').strip() in ('submit', 'submit -f')
                    and ('exceeded' in s.get('observation', '') or 'cancelled' in s.get('observation', ''))
                    and 'seconds' in s.get('observation', '') for s in tail)):
        return 'submission_timeout', 1
    return None, 0


def reserve_retry(state, reason, limit):
    history = state.setdefault("retries", [])
    if not reason or sum(x["reason"] == reason for x in history) >= limit:
        return False
    history.append({"reason": reason, "at": time.time(), "budget_reset": False})
    state["not_before"] = time.time() + min(300, 30 * 2 ** (len(history) - 1))
    return True
