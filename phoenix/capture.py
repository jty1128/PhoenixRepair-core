"""Capture a stopped owned checkout before cleanup, using the historical protocol."""
import json
from pathlib import Path
import shlex
import subprocess
import time

from phoenix.run_state import digest, read, lock
from phoenix.settings import write_json
from sweagent.agent.patch_capture import staged_diff_command


# Executed only inside the one label-verified container belonging to an attempt.
# Runtime authentication stays inside that container and is never printed.
QUIESCE = r'''
import os,pathlib,signal,time,urllib.request,sys
port=sys.argv[1]; runtime=None; token=None
for f in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  args=f.read_bytes().decode().split('\0')
  if '--auth-token' in args and any('swerex' in a for a in args):
   if '--port' in args and args[args.index('--port')+1]!=port:continue
   runtime=int(f.parent.name);token=args[args.index('--auth-token')+1];break
 except (OSError,ValueError,UnicodeError):pass
if runtime and token:
 try:
  urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+port+'/close',
    data=b'',method='POST',headers={'X-API-Key':token}),timeout=8).read()
 except Exception:pass
def parents(pid):
 out=set()
 while pid>0 and pid not in out:
  out.add(pid)
  try:pid=int(pathlib.Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[1])
  except OSError:break
 return out
keep={1}|parents(os.getpid())|(parents(runtime) if runtime else set())
for _ in range(20):
 active=[]
 for f in pathlib.Path('/proc').glob('[0-9]*/stat'):
  try:
   pid=int(f.parent.name);state=f.read_text().rsplit(')',1)[1].split()[0]
   if pid not in keep and state!='Z':active.append(pid)
  except OSError:pass
 if not active:break
 for pid in active:
  try:os.kill(pid,signal.SIGKILL)
  except ProcessLookupError:pass
 time.sleep(.1)
else:raise RuntimeError('Container commands did not stop; refusing a changing-tree capture')
print('quiesced')
'''


def containers(env, run_id, job_id, timeout=15):
    result = subprocess.run(["docker", "ps", "-aq", "--filter", "label=phoenixrepair.run=" + run_id,
                             "--filter", "label=phoenixrepair.job=" + job_id],
                            env=env, capture_output=True, text=True, check=True, timeout=timeout)
    return result.stdout.split()


def capture_checkout(request, env, attempt, *, official=False, timeout=60):
    """Capture only after worker stop/deadline and container quiescence."""
    attempt = Path(attempt)
    with lock(attempt / 'capture.lock'):
        previous = read(attempt / 'capture.json')
        if previous.get('status') == 'captured':
            if digest(attempt / 'captured.patch') != previous.get('patch_sha256'):
                raise ValueError('Captured patch changed')
            return previous
        return _capture_checkout(request, env, attempt, official=official, timeout=timeout)


def _capture_checkout(request, env, attempt, *, official=False, timeout=60):
    attempt = Path(attempt)
    audit = {"status": "capture_started", "model_calls_used": 0,
             "official_v2_staged_diff": official, "at": time.time()}
    deadline = time.monotonic() + timeout
    def remaining(limit):
        value = min(limit, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('Original capture allowance exhausted')
        return value
    try:
        ids = containers(env, request["run_id"], request["job_id"], timeout=remaining(15))
        if len(ids) != 1:
            raise RuntimeError("Expected exactly one owned container for capture")
        container = ids[0]
        inspect = subprocess.run(["docker", "inspect", container], env=env, capture_output=True,
                                 text=True, timeout=remaining(15), check=True)
        record = json.loads(inspect.stdout)[0]
        if (not record["State"]["Running"] or record["Config"]["Labels"].get("phoenixrepair.job") != request["job_id"]
                or record["Config"]["Labels"].get("phoenixrepair.run") != request["run_id"]):
            raise RuntimeError("Container ownership/state changed before capture")
        if request.get("image_id") and record["Image"] != request["image_id"]:
            raise RuntimeError("Container image differs from the pinned input")
        audit["container"] = container
        def execute(*args, limit=timeout):
            return subprocess.run(["docker", "exec", "--user", "0", container, *args], env=env,
                                  capture_output=True, timeout=remaining(limit), check=True).stdout
        execute("/opt/phoenix-python/bin/python3", "-c", QUIESCE, str(request["port"]), limit=15)
        repo = "/" + request["task"]["repo_name"]
        base = read(attempt / "history_isolation.json").get("snapshot_commit")
        head = execute("git", "-C", repo, "rev-parse", "HEAD").decode().strip()
        if not base or head != base:
            raise RuntimeError("Cannot capture without verified history-free base identity")
        target = "/root/phoenix_captured.patch"
        if official:
            command = staged_diff_command(repo, target)
        elif request["settings"]["model"] == "kimi-k2.7-code":
            command = ("cd " + shlex.quote(repo) + " && git add -N . && "
                       "git diff --binary --no-ext-diff HEAD > " + target)
        else:
            command = ("cd " + shlex.quote(repo) + " && git add -A && "
                       "git diff --binary --no-ext-diff HEAD > " + target)
        execute("bash", "-c", command)
        patch = execute("cat", target)
        (attempt / "captured.patch").write_bytes(patch)
        audit.update(status="captured", patch_sha256=digest(attempt / "captured.patch"),
                     patch_bytes=len(patch), snapshot_commit=head, image_id=record["Image"])
        if not official and request["settings"]["model"] != "kimi-k2.7-code":
            audit["patched_tree"] = execute("git", "-C", repo, "write-tree").decode().strip()
        return audit
    except Exception as exc:
        audit.update(status="capture_failed", error_type=type(exc).__name__)
        return audit
    finally:
        write_json(attempt / "capture.json", audit)
