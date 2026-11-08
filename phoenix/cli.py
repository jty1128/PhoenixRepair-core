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

from phoenix.settings import ROOT, PROFILES, fingerprint, load_instances, load_settings, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method", choices=("single", "tts", "our_method"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--model", choices=tuple(PROFILES))
    parser.add_argument("--instances", type=Path, help="JSON array or JSONL task manifest")
    parser.add_argument("--output", type=Path, help="New output directory; existing runs are never overwritten")
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
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", "--prepare-only", dest="dry_run", action="store_true",
                        help="Validate configuration and task inputs; no model or Docker calls")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.method, args.config or ROOT / "configs" / (args.method + ".yaml"), {
            k: getattr(args, k) for k in ("model", "instances", "output", "runtime", "api_base", "concurrency", "samples",
                                         "max_steps", "task_timeout", "localization_max_steps", "localization_timeout",
                                         "docker_host", "locations", "stage")})
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
        plan = dict(settings=settings, tasks=len(instances), instance_ids=[x["instance_id"] for x in instances],
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
        if output.exists() and any(output.iterdir()):
            raise ValueError("Output directory is not empty; choose --output for a new run")
        output.mkdir(parents=True, exist_ok=True)
        with (output / "controller.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            write_json(output / "plan.json", plan)
            write_json(output / "instances.json", instances)
            from phoenix.pipeline import run
            reports = run(instances, settings, key)
            counts = Counter(x["status"] for x in reports)
            write_json(output / "progress.json", {"status": "completed", "counts": dict(counts), "tasks": reports})
            print(json.dumps({"output": str(output), "counts": dict(counts), "review_calls": 0}, ensure_ascii=False))
            return 1 if any(x["status"] not in ("completed", "localized") for x in reports) else 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(f"Configuration/runtime error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
