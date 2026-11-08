"""Durable Docker stage lifecycle, migrated from the historical controllers."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import uuid

from phoenix.capture import capture_checkout, containers
from phoenix.recovery_profiles import recovery_profile
from phoenix.run_state import (bind, digest, find_worker, lock, owned_alive,
                               process_identity, read, reserve_retry, retry_reason,
                               stop_owned, verified_result)
from phoenix.settings import ROOT, fingerprint, model_config, write_json


class ControllerInterrupted(Exception):
    pass


def log_tail(path, limit=200_000):
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - limit))
        return stream.read().decode(errors="replace")


class DockerBackend:
    def __init__(self, settings, key):
        from phoenix.runtime import validate
        from phoenix.worker import prepare_tools
        validate(settings["runtime"])
        self.settings, self.key = settings, key
        self.output = Path(settings["output"])
        self.output.mkdir(parents=True, exist_ok=True)
        self.base = prepare_tools(self.output)["agent"]
        self.profile = recovery_profile(settings)
        with lock(self.output / "run-state.lock"):
            run = read(self.output / "run-state.json")
            if not run:
                run = {"run_id": uuid.uuid4().hex, "model": settings["model"],
                       "method": settings["method"], "recovery": self.profile}
                write_json(self.output / "run-state.json", run)
            if (run["model"], run["method"]) != (settings["model"], settings["method"]):
                raise ValueError("Cannot resume a run with another model/method")
        self.run_id = run["run_id"]
        self.stop_event = threading.Event()
        self.port_lock, self.ports = threading.Lock(), set()
        self.env = dict(os.environ, PHOENIX_API_KEY=key, LITELLM_LOCAL_MODEL_COST_MAP="True",
                        PHOENIX_API_USAGE_DB=str(self.output / "api-usage.sqlite3"))
        self.env["PHOENIX_ACCOUNT_ID"] = os.environ.get("PHOENIX_ACCOUNT_ID") or (
            "env-" + hashlib.sha256(key.encode()).hexdigest()[:16])
        if settings.get("docker_host"):
            self.env["DOCKER_HOST"] = settings["docker_host"]

    def allocate_port(self):
        with self.port_lock:
            for _ in range(100):
                with socket.socket() as stream:
                    stream.bind(("127.0.0.1", 0))
                    port = stream.getsockname()[1]
                if port not in self.ports:
                    self.ports.add(port)
                    return port
        raise RuntimeError("Unable to allocate a unique worker port")

    def image_id(self, task):
        with lock(self.output / "images.lock"):
            images = read(self.output / "images.json")
            from phoenix.localization import source_key
            source_image = read(self.output / 'source-cache' / source_key(task) / 'source.json').get('image_id')
            if source_image and task['image_name'] in images and images[task['image_name']] != source_image:
                raise ValueError('Patch image differs from the localization source image')
            image = images.get(task["image_name"], source_image or task["image_name"])
            result = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                                    env=self.env, capture_output=True, text=True, timeout=20)
            if result.returncode or not result.stdout.strip().startswith("sha256:"):
                raise RuntimeError("image_not_ready: preload the pinned task image")
            resolved = result.stdout.strip()
            if task["image_name"] in images and resolved != images[task["image_name"]]:
                raise ValueError("Pinned image identity changed")
            images[task["image_name"]] = resolved
            write_json(self.output / "images.json", images)
            return resolved

    def cleanup(self, job_id):
        ids = containers(self.env, self.run_id, job_id)
        if ids:
            subprocess.run(["docker", "rm", "-f", *ids], env=self.env, capture_output=True,
                           check=True, timeout=45)

    def _wait_until(self, timestamp):
        while time.time() < timestamp:
            if self.stop_event.wait(min(1, timestamp - time.time())):
                raise ControllerInterrupted("Controller stopped; paid work and live workers are preserved")

    def _monitor(self, request, attempt, child=None):
        record = read(attempt / "process.json") or find_worker(attempt / "request.json")
        if child is not None and not record:
            ident = process_identity(child.pid)
            if ident:
                record = {"pid": child.pid, "start_ticks": ident["start_ticks"],
                          "started_at": request["started_at"]}
        timed_out = read(attempt / "termination.json").get("reason") == "wall_timeout"
        while record and owned_alive(record):
            claimed = read(attempt / "process.json")
            if claimed:
                record = claimed
            if child is not None and record['pid'] != child.pid:
                # A late duplicate launcher lost the worker's exclusive lock.
                # Monitor the actual owner, not that short-lived launcher.
                child.wait(timeout=5)
                child = None
            if self.stop_event.is_set():
                raise ControllerInterrupted("Worker remains available for --resume")
            if child is not None and child.poll() is not None:
                break
            if time.time() >= request["deadline"]:
                timed_out = True
                write_json(attempt / "termination.json", {"reason": "wall_timeout", "at": time.time()})
                stop_owned(record, grace=min(10, request.get("capture_reserve", 60)))
                break
            time.sleep(.25)
        if child is not None:
            try:
                rc = child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if record:
                    stop_owned(record, grace=0)
                rc = child.wait(timeout=5)
        else:
            rc = 0 if read(attempt / "worker-status.json").get("status") == "completed" else None
        return rc, timed_out or read(attempt / "termination.json").get("reason") == "wall_timeout"

    def _read_attempt(self, request, attempt, rc, timed_out):
        checkpoint = attempt / "checkpoint.json"
        trajectory = checkpoint if checkpoint.exists() else attempt / "generate/author/author.traj"
        try:
            data = read(trajectory)
        except json.JSONDecodeError:
            # The upstream .traj writer is non-atomic. Never turn a truncated
            # file into a new uncharged trajectory; the budget is independent.
            data = {}
        info = data.get("info", {})
        state = read(attempt / "step-state.json")
        budget = read(attempt / "author_budget.json")
        calls = budget.get("attempted_calls", request.get("prior_calls", 0))
        steps = state.get("steps", request.get("steps_offset", 0) + len(data.get("trajectory", [])))
        steps = max(steps, read(attempt / "query-state.json").get("reserved_step", 0))
        patch = None
        submission = read(attempt / 'submission.json')
        for name in (("P0.patch", "submitted.patch") if self.settings["method"] == "our_method"
                     else ("submitted.patch",)):
            if (attempt / name).is_file() and ((attempt / name).read_bytes().strip()
                    or self.settings['method'] != 'our_method' and str(submission.get('exit_status')).startswith('submitted')):
                patch = attempt / name
                break
        if patch is None and info.get("submission"):
            patch = attempt / "submitted.patch"
            patch.write_text(info["submission"])
        if patch is None and (attempt / "captured.patch").exists():
            capture = read(attempt / "capture.json")
            if capture.get("status") == "captured":
                if digest(attempt / "captured.patch") != capture.get("patch_sha256"):
                    raise ValueError("Saved capture changed; refusing to reuse it")
                patch = attempt / "captured.patch"
        exit_status = read(attempt / "submission.json").get("exit_status") or info.get("exit_status")
        log = log_tail(attempt / "generation.log")
        reason, limit = retry_reason(log, exit_status,
                                     model_started=(calls > request.get('prior_calls', 0)
                                                    or steps > request.get('prior_steps', 0)),
                                     trajectory=data.get("trajectory", []))
        if self.settings["method"] != "our_method" and reason == "container_start_timeout":
            limit = self.profile["environment_start_retries"]
        if not patch and (timed_out or reason or rc != 0):
            capture = capture_checkout(request, self.env, attempt,
                official=self.settings["model"] != "kimi-k2.7-code" and self.settings["method"] != "our_method" and timed_out,
                timeout=request.get("capture_reserve", 60) or 60)
            if capture["status"] == "captured":
                patch = attempt / "captured.patch"
        if not reason and (rc is None or rc < 0) and not timed_out:
            reason, limit = "interrupted_worker", 1
        return {"patch": patch, "trajectory": trajectory if data.get("history") else None,
                "calls": calls, "steps": max(request.get("prior_steps", 0), steps),
                "execution_seconds": state.get("execution_seconds", request.get("prior_execution_seconds", 0)),
                "exit_status": exit_status, "reason": reason, "retry_limit": limit,
                "timed_out": timed_out, "returncode": rc, "model_stats": info.get("model_stats", {}),
                "mechanical": read(attempt / "P0.json").get("mechanical_validation", {}),
                "capture": read(attempt / "capture.json")}

    def _finish(self, stage, state, observed, *, recovered=False):
        patch = observed["patch"].read_bytes()
        (stage / "final.patch").write_bytes(patch)
        if self.settings["method"] == "our_method":
            (stage / "P0.patch").write_bytes(patch)
            write_json(stage / "P0.json", {"model_calls": state["calls"],
                "mechanical_validation": observed["mechanical"], "review_calls": 0, "repair_calls": 0})
        result = dict(status="completed", patch=str(stage / "final.patch"),
            has_patch=bool(patch.strip()),
            patch_sha256=digest(stage / "final.patch"), steps=state["steps"], model_calls=state["calls"],
            exit_status=observed["exit_status"] or ("wall_timeout_partial" if observed["timed_out"] else "submitted (recovered)"),
            model_stats=observed["model_stats"], returncode=observed["returncode"],
            timed_out=observed["timed_out"], recovery_used=recovered or bool(state["retries"]),
            attempts=state["attempts"], active_seconds=state["spent_seconds"],
            budget_reset=False, review_calls=0, repair_calls=0, benchmark_evaluated=False)
        write_json(stage / "result.json", result)
        state.update(status="completed", active=None)
        write_json(stage / "execution.json", state)
        return result

    def generate(self, task, stage, *, allowance, final_stage, candidate=None, summary=None, num_candidates=0):
        stage = Path(stage)
        stage.mkdir(parents=True, exist_ok=True)
        identity = dict(task=task, method=self.settings["method"], model=self.settings["model"],
            model_config=model_config(self.settings["model"], self.settings["api_base"],
                                      p0=self.settings["method"] == "our_method"),
            max_steps=self.settings["max_steps"], task_timeout=self.settings["task_timeout"],
            allowance=allowance, final_stage=final_stage, candidate=candidate, summary=summary,
            num_candidates=num_candidates, base_agent=self.base)
        with lock(stage / "stage.lock", blocking=False):
            bind(stage / "identity.json", identity)
            cached = verified_result(stage)
            if cached:
                return cached
            state = read(stage / "execution.json") or dict(status="pending", attempts=0,
                spent_seconds=0., execution_seconds=0., calls=0, steps=0, retries=[], active=None, recovery=None, replay_attempts=0)
            if state.get("status") == "failed":
                raise RuntimeError("Stage exhausted its bounded recovery policy; see " + str(stage / "execution.json"))
            while state["attempts"] < 12 or state.get("active"):
                self._wait_until(state.get("not_before", 0))
                mode = (state.get("recovery") or {}).get("mode")
                if not state.get("active"):
                    remaining = self.settings["task_timeout"] - state["spent_seconds"]
                    if mode != "submission" and (remaining <= 0 or state["steps"] >= self.settings["max_steps"]
                            or self.settings["method"] == "our_method" and state["calls"] >= allowance):
                        break
                    if mode == "submission":
                        if state["replay_attempts"] >= 2:
                            break
                        remaining = self.settings.get("submission_recovery_timeout", 600)
                    try:
                        image_id = self.image_id(task)
                    except (RuntimeError, subprocess.SubprocessError):
                        if reserve_retry(state, "image_not_ready", 3):
                            write_json(stage / "execution.json", state)
                            continue
                        break
                    state["attempts"] += 1
                    if mode == "submission":
                        state["replay_attempts"] += 1
                    attempt = stage / "attempts" / f"{state['attempts']:03d}"
                    attempt.mkdir(parents=True, exist_ok=True)
                    reserve = (min(self.profile["tts_capture_reserve_seconds"], remaining / 2)
                               if self.settings["method"] != "our_method" else 0)
                    now = time.time()
                    history_path = (state.get("recovery") or {}).get("trajectory")
                    offset = state["steps"] - (len(read(history_path).get("trajectory", [])) if history_path else 0)
                    request = dict(task=task, settings=self.settings, stage_dir=str(attempt), base_agent=self.base,
                        run_id=self.run_id, job_id=fingerprint([str(stage), state["attempts"]])[:24],
                        port=self.allocate_port(), image_id=image_id, candidate=candidate, summary=summary,
                        num_candidates=num_candidates, generation_allowance=allowance, final_stage=final_stage,
                        prior_calls=state["calls"], prior_steps=state["steps"], recovery=state.get("recovery"),
                        prior_execution_seconds=state.get("execution_seconds", 0),
                        steps_offset=max(0, offset),
                        managed_recovery=True, started_at=now, deadline=now + remaining - reserve,
                        capture_reserve=reserve or 60)
                    if (attempt / "request.json").exists():
                        self.ports.discard(request["port"])
                        request = read(attempt / "request.json")
                    else:
                        write_json(attempt / "request.json", request)
                    state["active"] = str(attempt)
                    write_json(stage / "execution.json", state)
                else:
                    attempt = Path(state["active"])
                    request = read(attempt / "request.json")
                record = read(attempt / "process.json") or find_worker(attempt / "request.json")
                child = None
                # A finished or dead recorded worker is inspected, never
                # silently replaced by a fresh uncharged worker.
                if not record and not (attempt / "worker-status.json").exists():
                    with (attempt / "generation.log").open("a") as log:
                        child = subprocess.Popen([sys.executable, "-m", "phoenix.worker", str(attempt / "request.json")],
                            cwd=ROOT, env=self.env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                rc, timed_out = self._monitor(request, attempt, child)
                # On controller interruption _monitor raises before cleanup:
                # the exact live worker is adopted by the next --resume.
                observed = self._read_attempt(request, attempt, rc, timed_out)
                self.cleanup(request["job_id"])
                self.ports.discard(request["port"])
                terminal = read(attempt / 'worker-status.json')
                ended = (terminal.get('at', time.time()) if terminal.get('status') in ('completed', 'failed')
                         else read(attempt / 'termination.json').get('at', time.time()))
                if (request.get("recovery") or {}).get("mode") != "submission":
                    state["spent_seconds"] += max(0, min(ended, request["deadline"]) - request["started_at"])
                state.update(active=None, calls=max(state["calls"], observed["calls"]),
                             steps=max(state["steps"], observed["steps"]),
                             execution_seconds=max(state.get("execution_seconds", 0), observed["execution_seconds"]))
                patch = observed["patch"]
                if mode == "submission" and not observed["mechanical"].get("valid"):
                    # Keep the original immutable seed even if replay failed
                    # halfway through applying it in a disposable checkout.
                    write_json(stage / "execution.json", state)
                    continue
                if patch and self.settings["method"] != "our_method":
                    if (patch.stat().st_size and timed_out) or rc == 0 or str(observed["exit_status"]).startswith("submitted"):
                        return self._finish(stage, state, observed, recovered=timed_out)
                if patch and self.settings["method"] == "our_method":
                    if observed["mechanical"].get("valid"):
                        return self._finish(stage, state, observed, recovered=mode == "submission")
                    # First submission or a bounded terminal capture is repaired
                    # mechanically, never replaced by another author solution.
                    mode = "submission" if (
                        (attempt / "P0.patch").exists() or (attempt / "submitted.patch").exists()
                        or timed_out or observed["reason"] == "submission_timeout"
                        or state["calls"] >= allowance) else "continue"
                    recovery = {"mode": mode, "patch": str(patch), "patch_sha256": digest(patch)}
                    if mode == "submission" and not patch.read_bytes().strip():
                        break
                    if patch.name == "captured.patch":
                        recovery["patched_tree"] = observed["capture"].get("patched_tree")
                    if mode == "continue" and observed["trajectory"]:
                        recovery.update(trajectory=str(observed["trajectory"]),
                                        trajectory_sha256=digest(observed["trajectory"]))
                    state["recovery"] = recovery
                    if mode == "submission":
                        write_json(stage / "execution.json", state)
                        continue
                elif patch and observed["reason"] and observed["trajectory"]:
                    state["recovery"] = {"mode": "continue", "patch": str(patch), "patch_sha256": digest(patch),
                        "trajectory": str(observed["trajectory"]), "trajectory_sha256": digest(observed["trajectory"])}
                if mode == "submission":
                    # A failed zero-call replay has at most one further try.
                    write_json(stage / "execution.json", state)
                    continue
                if observed["reason"] == "portable_runtime_unavailable" and not any(
                        x["reason"] == observed["reason"] for x in state["retries"]):
                    from phoenix.runtime_repair import repair
                    with lock(self.output / "runtime-repair.lock"):
                        write_json(attempt / "runtime-repair.json", repair(Path(self.settings["runtime"])))
                if not reserve_retry(state, observed["reason"], observed["retry_limit"]):
                    break
                write_json(stage / "execution.json", state)
                print(f"[recovery] {task['instance_id']} {stage.name}: {observed['reason']}; "
                      f"calls={state['calls']}, steps={state['steps']}; budget preserved", flush=True)
            state.update(status="failed", active=None)
            write_json(stage / "execution.json", state)
            write_json(stage / "result.json", dict(status="failed", model_calls=state["calls"],
                steps=state["steps"], attempts=state["attempts"], budget_reset=False,
                review_calls=0, repair_calls=0, error="Bounded recovery exhausted; original work retained"))
            raise RuntimeError("Generation/recovery failed: " + str(stage))
