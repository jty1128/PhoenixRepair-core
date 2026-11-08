"""Small bounded schedulers for single sampling, TTS, and the P0 method."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

from phoenix.settings import ROOT, fingerprint, localization_config, write_json
from phoenix.summary import summarize


def task_jobs(method, samples=3, candidates=1, max_calls=200):
    if method == "single":
        return [dict(stage="sample-1", kind="sample", final=True, allowance=max_calls)]
    if method == "tts":
        return [dict(stage=f"sample-{i}", kind="sample", final=False, allowance=max_calls) for i in range(1, samples + 1)] + [
            dict(stage="summary", kind="summary", final=False), dict(stage="final-sample", kind="final", final=True, allowance=max_calls)]
    if candidates < 1:
        raise ValueError("At least one complete localization candidate is required")
    if max_calls <= 40:
        raise ValueError("P0 preserves the original 40-call reservation; max_steps must exceed 40")
    if candidates == 1:
        return [dict(stage="candidate-1", kind="candidate", final=True, allowance=max_calls - 40)]
    candidate_pool = max_calls // 2
    if candidates > candidate_pool:
        raise ValueError("Too many candidates for the shared task budget")
    return [dict(stage=f"candidate-{i+1}", kind="candidate", final=False,
                 allowance=candidate_pool // candidates + (i < candidate_pool % candidates)) for i in range(candidates)] + [
        dict(stage="summary", kind="summary", final=False),
        dict(stage="final", kind="final", final=True, allowance=max_calls - candidate_pool - 1 - 40)]


class DockerBackend:
    def __init__(self, settings, key):
        from phoenix.runtime import validate
        from phoenix.worker import prepare_tools
        self.settings, self.key = settings, key
        validate(settings["runtime"])
        self.output = Path(settings["output"])
        self.output.mkdir(parents=True, exist_ok=True)
        self.base = prepare_tools(self.output)["agent"]
        self.run_id = uuid.uuid4().hex
        self.port_lock, self.ports = threading.Lock(), set()
        self.env = dict(os.environ, PHOENIX_API_KEY=key, LITELLM_LOCAL_MODEL_COST_MAP="True",
                        PHOENIX_API_USAGE_DB=str(self.output / "api-usage.sqlite3"))
        # P0's shared request pacing needs an account identifier. A standalone
        # checkout has no server-specific account map; use a stable opaque ID.
        self.env["PHOENIX_ACCOUNT_ID"] = os.environ.get("PHOENIX_ACCOUNT_ID") or (
            "env-" + hashlib.sha256(key.encode()).hexdigest()[:16])
        if settings.get("docker_host"):
            self.env["DOCKER_HOST"] = settings["docker_host"]

    def allocate_port(self):
        with self.port_lock:
            for _ in range(100):
                with socket.socket() as s:
                    s.bind(("127.0.0.1", 0))
                    port = s.getsockname()[1]
                if port not in self.ports:
                    self.ports.add(port)
                    return port
        raise RuntimeError("Unable to allocate a unique worker port")

    def generate(self, task, stage, *, allowance, final_stage, candidate=None, summary=None, num_candidates=0):
        stage.mkdir(parents=True, exist_ok=True)
        job_id = fingerprint([task["instance_id"], str(stage)])[:24]
        request = dict(task=task, settings=self.settings, stage_dir=str(stage), base_agent=self.base,
                       run_id=self.run_id, job_id=job_id, port=self.allocate_port(), candidate=candidate,
                       summary=summary, num_candidates=num_candidates,
                       generation_allowance=allowance, final_stage=final_stage)
        write_json(stage / "worker-request.json", request)
        returncode, timed_out = None, False
        process = None
        try:
            with (stage / "generation.log").open("w") as log:
                process = subprocess.Popen([sys.executable, "-m", "phoenix.worker", str(stage / "worker-request.json")],
                    cwd=ROOT, env=self.env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    returncode = process.wait(timeout=self.settings["task_timeout"])
                except subprocess.TimeoutExpired:
                    timed_out = True
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
            self.cleanup(job_id)
        traj = stage / "generate/author/author.traj"
        data = json.loads(traj.read_text()) if traj.exists() else {}
        info = data.get("info", {})
        patch = info.get("submission") or ""
        if self.settings["method"] == "our_method" and (stage / "P0.patch").exists():
            patch = (stage / "P0.patch").read_text()
        (stage / "final.patch").write_text(patch)
        budget = stage / "author_budget.json"
        calls = json.loads(budget.read_text())["attempted_calls"] if budget.exists() else info.get("model_stats", {}).get("api_calls", 0)
        result = dict(status="completed" if patch.strip() and not timed_out and (returncode == 0 or info.get("exit_status") == "submitted") else "failed",
                      patch=str(stage / "final.patch"), patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
                      steps=len(data.get("trajectory", [])), model_calls=calls, exit_status=info.get("exit_status"),
                      returncode=returncode, timed_out=timed_out, model_stats=info.get("model_stats", {}),
                      review_calls=0, repair_calls=0, benchmark_evaluated=False)
        write_json(stage / "result.json", result)
        if result["status"] != "completed":
            raise RuntimeError("Generation failed or produced no patch: " + str(stage / "generation.log"))
        return result

    def cleanup(self, job_id):
        try:
            result = subprocess.run(["docker", "ps", "-aq", "--filter", "label=phoenixrepair.run=" + self.run_id,
                "--filter", "label=phoenixrepair.job=" + job_id], env=self.env, capture_output=True, text=True, timeout=30, check=True)
            if result.stdout.split():
                subprocess.run(["docker", "rm", "-f", *result.stdout.split()], env=self.env, capture_output=True, timeout=45, check=True)
        except (subprocess.SubprocessError, OSError) as exc:
            print("Container cleanup failed for owned job " + job_id + ": " + type(exc).__name__, flush=True)


def load_location(root, task, model):
    directory = Path(root) / model / task["instance_id"]
    instances_path = Path(root) / "instances.json"
    if not instances_path.is_file():
        raise ValueError("Frozen locations require instances.json to verify issue and base commit")
    source = {x["instance_id"]: x for x in json.loads(instances_path.read_text())}
    if source.get(task["instance_id"]) != task:
        raise ValueError("Frozen localization task/issue/base identity mismatch")
    artifact = json.loads((directory / "location_candidates.json").read_text())
    report = json.loads((directory / "report.json").read_text())
    if report.get("status") not in ("localized", "handoff_ready"):
        raise ValueError("Localization has no complete published plan")
    if report.get("model", model) != model or artifact.get("review_model", model) != model:
        raise ValueError("A model must use its own localization")
    candidates = artifact.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("No complete localization candidates")
    if any(not isinstance(c, dict) or not c.get("id") for c in candidates):
        raise ValueError("Malformed localization candidate")
    if len({c["id"] for c in candidates}) != len(candidates):
        raise ValueError("Duplicate candidate IDs")
    # Use the same structural contract as the source-only publisher. This
    # validates coverage/references, not whether a proposed repair is correct.
    from phoenix.localization import LIB
    shared = candidates[0].get("requirements")
    for candidate in candidates:
        if candidate.get("planning_mode") != "complete_strategy_v1":
            raise ValueError("Frozen locations must contain complete strategies")
        LIB["validate_candidate_contract"](candidate)
        LIB["complete_strategy"]()["validate_plan"](candidate, shared=shared)
    return artifact


def run_task(task, settings, backend, key, candidates=None, summary_fn=summarize):
    directory = Path(settings["output"]) / settings["model"] / task["instance_id"]
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "instance.json", task)
    method = settings["method"]
    jobs = task_jobs(method, settings["samples"], len(candidates or []) if method == "our_method" else 1, settings["max_steps"])
    write_json(directory / "plan.json", jobs)
    results, summary, used = [], None, 0
    try:
        for job in jobs:
            stage = directory / job["stage"]
            if job["kind"] == "summary":
                summary = summary_fn(task, results, settings, stage, key, shared_budget=method == "our_method")
                if method == "our_method":
                    used += 1
                continue
            candidate = candidates[len(results)] if job["kind"] == "candidate" else None
            allowance = job["allowance"]
            if method == "our_method" and job["kind"] == "final":
                allowance = settings["max_steps"] - used - 40
                if allowance < 1:
                    raise RuntimeError("No generation budget remains for final P0")
            result = backend.generate(task, stage, allowance=allowance, final_stage=job["final"],
                candidate=candidate, summary=summary if job["kind"] == "final" else None,
                num_candidates=len(results))
            results.append(result)
            used += result["model_calls"]
        final = results[-1]
        patch = Path(final["patch"]).read_bytes()
        (directory / "final.patch").write_bytes(patch)
        if method == "our_method":
            (directory / "P0.patch").write_bytes(patch)
        report = dict(status="completed", model=settings["model"], instance_id=task["instance_id"],
            method=method, patch=str(directory / "final.patch"), model_calls=used,
            review_calls=0, repair_calls=0, output_stage="P0" if method == "our_method" else "final",
            benchmark_evaluated=False, stages=results)
    except Exception as exc:
        report = dict(status="failed", model=settings["model"], instance_id=task["instance_id"],
            error=type(exc).__name__ + ": " + str(exc), stages=results, review_calls=0, repair_calls=0)
    write_json(directory / "report.json", report)
    return report


def parallel(items, fn, workers):
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): item for item in items}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(f"[{len(results)}/{len(items)}] {result.get('instance_id', '?')}: {result['status']}", flush=True)
    return results


def run(instances, settings, key):
    output = Path(settings["output"])
    locations = settings.get("locations")
    if settings.get("docker_host"):
        os.environ["DOCKER_HOST"] = settings["docker_host"]
    ready = instances
    failures = []
    if settings["method"] == "our_method" and settings["stage"] != "patch" and not locations:
        from phoenix import localization
        locations = str(output / "localization")
        write_json(Path(locations) / "instances.json", instances)
        cache = output / "source-cache"
        export_lock = threading.Semaphore(2)

        def locate(task):
            try:
                with export_lock:
                    source = localization.export_source(cache, task)
                profile = {"model": settings["model"], "account": "environment"}
                return localization.run_task(task, source, Path(locations), profile, key,
                                             localization_config(settings), settings["api_base"])
            except Exception as exc:
                result = dict(status="failed", instance_id=task["instance_id"], error=type(exc).__name__ + ": " + str(exc))
                write_json(Path(locations) / settings["model"] / task["instance_id"] / "report.json", result)
                return result
        localized = parallel(instances, locate, settings["concurrency"])
        failures = [x for x in localized if x["status"] != "localized"]
        succeeded = {x["instance_id"] for x in localized if x["status"] == "localized"}
        ready = [x for x in instances if x["instance_id"] in succeeded]
        if settings["stage"] == "localization":
            return localized
    if settings["method"] == "our_method" and not locations:
        raise ValueError("Patch-only mode requires --locations")
    backend = DockerBackend(settings, key) if ready else None

    def solve(task):
        try:
            candidates = None
            if settings["method"] == "our_method":
                artifact = load_location(locations, task, settings["model"])
                write_json(output / settings["model"] / task["instance_id"] / "localization.json", artifact)
                candidates = artifact["candidates"]
            return run_task(task, settings, backend, key, candidates)
        except Exception as exc:
            result = dict(status="failed", instance_id=task["instance_id"], error=type(exc).__name__ + ": " + str(exc))
            write_json(output / settings["model"] / task["instance_id"] / "report.json", result)
            return result
    return failures + parallel(ready, solve, settings["concurrency"])
