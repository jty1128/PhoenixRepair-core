"""Small bounded schedulers for single sampling, TTS, and the P0 method."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import json
import os
from pathlib import Path
import threading
import time

from phoenix.settings import localization_config, write_json
from phoenix.summary import summarize
from phoenix.run_state import bind, digest
from phoenix.backend import DockerBackend, ControllerInterrupted


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
    bind(directory / "task-identity.json", dict(task=task, jobs=jobs, candidates=candidates,
                                               model=settings["model"], method=method))
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
            # Test/custom backends also receive the same immutable stage receipt.
            if not result.get("patch_sha256"):
                result["patch_sha256"] = digest(Path(result["patch"]))
            result.setdefault("status", "completed")
            write_json(stage / "result.json", result)
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
    except ControllerInterrupted as exc:
        report = dict(status="interrupted", model=settings["model"], instance_id=task["instance_id"],
            error=str(exc), stages=results, review_calls=0, repair_calls=0)
    except Exception as exc:
        report = dict(status="failed", model=settings["model"], instance_id=task["instance_id"],
            error=type(exc).__name__ + ": " + str(exc), stages=results, review_calls=0, repair_calls=0)
    write_json(directory / "report.json", report)
    return report


def parallel(items, fn, workers, settings=None, stop_event=None):
    results = []
    pool = ThreadPoolExecutor(max_workers=workers)
    pending = iter(items)
    active, exhausted = {}, False
    sampler = policy = None
    if settings and settings.get("adaptive_io"):
        from phoenix.adaptive_io import IOSampler, AdmissionPolicy
        sampler = IOSampler(settings["io_device"])
        policy = AdmissionPolicy(initial=workers, maximum=workers, minimum=min(4, workers))
    try:
        while active or not exhausted:
            limit = workers
            if sampler:
                state = policy.update(sampler.sample(), time.monotonic(), len(active))
                state["environment_start_concurrency"] = min(state["environment_start_concurrency"],
                                                           settings["environment_start_concurrency"])
                state["local_command_concurrency"] = min(state["local_command_concurrency"],
                                                       settings["local_command_concurrency"])
                write_json(Path(settings["output"]) / "resource_policy.json", state)
                limit = state["total"]
            while not exhausted and len(active) < limit and not (stop_event and stop_event.is_set()):
                try:
                    item = next(pending)
                except StopIteration:
                    exhausted = True
                    break
                active[pool.submit(fn, item)] = item
            if stop_event and stop_event.is_set():
                exhausted = True
            if not active:
                break
            done, _ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
            for future in done:
                active.pop(future)
                result = future.result()
                results.append(result)
                print(f"[{len(results)}/{len(items)}] {result.get('instance_id', '?')}: {result['status']}", flush=True)
    except BaseException:
        if stop_event:
            stop_event.set()
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)
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
                result = dict(status="infrastructure_failed", instance_id=task["instance_id"], error=type(exc).__name__ + ": " + str(exc))
                write_json(Path(locations) / settings["model"] / task["instance_id"] / "report.json", result)
                return result
        localized = parallel(instances, locate, settings["concurrency"], settings)
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
    return failures + parallel(ready, solve, settings["concurrency"], settings,
                               getattr(backend, "stop_event", None))
