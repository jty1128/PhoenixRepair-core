"""Command-line entry point for the three method configurations."""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import signal

from phoenix.settings import ROOT, PROFILES, fingerprint, load_instances, load_settings, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method", choices=("single", "tts", "our_method"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--model", choices=tuple(PROFILES))
    parser.add_argument("--instances", type=Path, help="JSON array or JSONL task manifest")
    parser.add_argument("--output", type=Path, help="Run directory; use --resume to continue the same run")
    parser.add_argument("--resume", action="store_true", help="Adopt this run's workers and reuse verified stages without resetting budgets")
    parser.add_argument("--runtime", type=Path, help="Prepared portable tool runtime (or PHOENIX_RUNTIME)")
    parser.add_argument("--api-base")
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--samples", type=int, help="Independent TTS samples, default 3")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--task-timeout", type=int)
    parser.add_argument("--localization-max-steps", type=int, help="Localization cap, historical default 150; patch budget is separate")
    parser.add_argument("--localization-timeout", type=int, help="Optional localization wall-time cap; historical run used a step cap")
    parser.add_argument("--docker-host")
    parser.add_argument("--locations", type=Path, help="Reuse same-model locations with verified task identity")
    parser.add_argument("--stage", choices=("all", "localization", "patch"))
    parser.add_argument("--adaptive-io", action="store_true", default=None)
    parser.add_argument("--io-device", help="Linux disk name used by the historical adaptive I/O policy")
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", "--prepare-only", dest="dry_run", action="store_true",
                        help="Validate configuration and task inputs; no model or Docker calls")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.method, args.config or ROOT / "configs" / (args.method + ".yaml"), {
            k: getattr(args, k) for k in ("model", "instances", "output", "runtime", "api_base", "concurrency", "samples",
                                         "max_steps", "task_timeout", "localization_max_steps", "localization_timeout",
                                         "docker_host", "locations", "stage", "adaptive_io", "io_device")})
        settings["resume"] = args.resume
        if not settings.get("instances"):
            raise ValueError("Set --instances /path/to/tasks.jsonl or INSTANCES in .env")
        if args.limit < 0:
            raise ValueError("limit cannot be negative")
        instances = load_instances(Path(settings["instances"]), args.instance_id, args.limit)
        if args.method == "our_method" and settings["max_steps"] <= 40:
            raise ValueError("P0's historical generation budget requires max_steps > 40")
        if args.method == "our_method" and settings["stage"] == "patch" and not settings.get("locations"):
            raise ValueError("--stage patch requires --locations")
        if settings["stage"] == "localization" and settings.get("locations"):
            raise ValueError("--locations is for patch generation; omit it when generating new locations")
        if settings.get("locations"):
            from phoenix.pipeline import load_location
            for task in instances:
                load_location(settings["locations"], task, settings["model"])
        plan = dict(recovery_format=1, settings=settings, tasks=len(instances), instance_ids=[x["instance_id"] for x in instances],
                    task_manifest_sha256=fingerprint(instances), model_profile=PROFILES[settings["model"]],
                    review_calls_enabled=False, repair_stage_enabled=False,
                    method_output="P0" if args.method == "our_method" else "final.patch",
                    old_patch_seed=False, evaluation_feedback_input=False)
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        key = os.environ.get("PHOENIX_API_KEY") or os.environ.get("API_KEY")
        if not key:
            raise ValueError("Set PHOENIX_API_KEY (or API_KEY) in your environment; keys are never command-line arguments")
        if settings["stage"] != "localization" and not settings.get("runtime"):
            raise ValueError("Set --runtime or PHOENIX_RUNTIME; see runtime preparation in README.md")
        # Check Docker before creating a run or issuing a model request.
        env = dict(os.environ)
        if settings.get("docker_host"):
            env["DOCKER_HOST"] = settings["docker_host"]
        subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], env=env,
                       capture_output=True, check=True, timeout=20)
        output = Path(settings["output"])
        exists = output.exists() and any(output.iterdir())
        if exists and not args.resume:
            raise ValueError("Output directory is not empty; use --resume or choose a new --output")
        if args.resume and not (output / "plan.json").is_file():
            raise ValueError("--resume requires a compatible saved run plan")
        output.mkdir(parents=True, exist_ok=True)
        with (output / "controller.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.resume:
                previous = json.loads((output / "plan.json").read_text())
                if previous.get("recovery_format") != 1:
                    raise ValueError("This run predates durable recovery; use a new output directory")
                immutable = ("model", "method", "api_base", "max_steps", "task_timeout", "samples",
                             "localization_max_steps", "localization_timeout", "locations", "stage",
                             "runtime", "docker_host", "container_cpus", "container_memory", "pin_cpus")
                if (previous.get("task_manifest_sha256") != plan["task_manifest_sha256"] or
                    any(previous["settings"].get(k) != settings.get(k) for k in immutable)):
                    raise ValueError("Resume must preserve task inputs, model, method, locations and original budgets")
                write_json(output / "resume-settings.json", settings)
            else:
                write_json(output / "plan.json", plan)
                write_json(output / "instances.json", instances)
            if not settings["adaptive_io"]:
                write_json(output / "resource_policy.json", {"mode": "fixed", "total": settings["concurrency"],
                    "environment_start_concurrency": settings["environment_start_concurrency"],
                    "local_command_concurrency": settings["local_command_concurrency"]})
            from phoenix.pipeline import run
            previous_term = signal.getsignal(signal.SIGTERM)
            def interrupted(*_):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, interrupted)
            try:
                reports = run(instances, settings, key)
            finally:
                signal.signal(signal.SIGTERM, previous_term)
            counts = Counter(x["status"] for x in reports)
            write_json(output / "progress.json", {"status": "completed", "counts": dict(counts), "tasks": reports})
            print(json.dumps({"output": str(output), "counts": dict(counts), "review_calls": 0}, ensure_ascii=False))
            return 1 if any(x["status"] not in ("completed", "localized") for x in reports) else 0
    except KeyboardInterrupt:
        if "output" in locals():
            write_json(output / "controller-interrupted.json", {"status": "interrupted",
                "resume_required": True, "live_workers_preserved": True, "budget_reset": False})
        print("Interrupted. Use the same command with --resume; existing workers and charged work are preserved.",
              file=sys.stderr, flush=True)
        return 130
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(f"Configuration/runtime error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    code = main()
    if code == 130:
        # HTTP threads may be waiting on a provider. Their requests are already
        # durably charged; don't wait for them while owned subprocesses continue.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    raise SystemExit(code)
