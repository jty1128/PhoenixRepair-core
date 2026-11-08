"""Shared, explicit profiles for all three methods."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
FIELDS = ("instance_id", "problem_statement", "base_commit", "repo_name", "image_name")
PROFILES = {
    "glm-5.2": dict(max_tokens=8192, timeout=240, tool_choice="required", thinking=False, task_timeout=7200),
    "qwen3.7-plus": dict(max_tokens=32768, timeout=600, tool_choice="auto", thinking=False, task_timeout=7200),
    "kimi-k2.7-code": dict(max_tokens=32768, timeout=240, tool_choice="auto", thinking=True, task_timeout=3000),
}


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def component(value, name):
    if not isinstance(value, str) or not value or value in (".", "..") or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"{name} must be a nonempty single path component")
    return value


def load_instances(path: Path, selected=(), limit=0):
    text = path.read_text()
    rows = json.loads(text) if text.lstrip().startswith("[") else [json.loads(x) for x in text.splitlines() if x.strip()]
    if not isinstance(rows, list) or not rows:
        raise ValueError("The manifest must contain a nonempty list of tasks")
    clean, seen = [], set()
    for row in rows:
        task = {key: row[key] for key in FIELDS}
        if any(not isinstance(v, str) or not v.strip() for v in task.values()):
            raise ValueError("All five task fields must be nonempty strings")
        component(task["instance_id"], "instance_id")
        component(task["repo_name"], "repo_name")
        if task["instance_id"] in seen:
            raise ValueError("Duplicate instance_id: " + task["instance_id"])
        seen.add(task["instance_id"])
        clean.append(task)
    if set(selected) - seen:
        raise ValueError("Requested task IDs are absent from the manifest")
    clean = [x for x in clean if not selected or x["instance_id"] in selected]
    return clean[:limit] if limit else clean


def model_config(model, api_base, *, p0=False):
    profile = PROFILES[model]
    data = {
        "name": "openai/" + model, "api_base": api_base, "api_key": "$PHOENIX_API_KEY",
        "temperature": 0.0, "top_p": 1.0, "per_instance_cost_limit": 0,
        "total_cost_limit": 0, "per_instance_call_limit": 0, "max_input_tokens": 131072,
        "context_compaction_threshold": 120000, "context_compaction_target": 110000,
        "context_keep_first_rounds": 5, "context_keep_last_rounds": 5,
        "retry": {"retries": 1 if p0 else 20, "min_wait": 10, "max_wait": 120},
        "completion_kwargs": {
            "extra_body": {"enable_thinking": profile["thinking"]},
            "max_tokens": profile["max_tokens"], "timeout": profile["timeout"],
            "tool_choice": profile["tool_choice"], "num_retries": 0,
        },
    }
    if profile["thinking"]:
        data["preserve_thinking"] = True
        data["completion_kwargs"]["extra_body"]["preserve_thinking"] = True
    if p0:
        data["request_min_interval"] = 2
    return data


def load_settings(mode, config_path, overrides):
    data = yaml.safe_load(config_path.read_text()) or {}
    allowed = {"model", "instances", "output", "runtime", "api_base", "concurrency", "samples", "max_steps",
               "task_timeout", "locations", "stage", "docker_host", "container_cpus", "container_memory",
               "localization_max_steps", "localization_timeout"}
    if set(data) - allowed:
        raise ValueError("Unknown configuration fields: " + ", ".join(sorted(set(data) - allowed)))
    data.update({k: v for k, v in overrides.items() if v is not None})
    data.setdefault("model", "glm-5.2")
    if data["model"] not in PROFILES:
        raise ValueError("Unknown model profile: " + data["model"])
    if mode == "our_method" and data["model"] not in ("glm-5.2", "qwen3.7-plus"):
        raise ValueError("The published P0 method has GLM and Qwen profiles; Kimi is available for single/TTS")
    for key, env in [("instances", "INSTANCES"), ("runtime", "PHOENIX_RUNTIME"), ("docker_host", "DOCKER_HOST"), ("api_base", "API_BASE")]:
        if not data.get(key) and os.environ.get(env):
            data[key] = os.environ[env]
    data.setdefault("api_base", API_BASE)
    data["api_base"] = data["api_base"] or API_BASE
    data.setdefault("output", "runs/" + mode)
    data.setdefault("concurrency", 4)
    data.setdefault("samples", 3)
    data.setdefault("max_steps", 200)
    data.setdefault("localization_max_steps", 150)
    data.setdefault("stage", "all")
    data.setdefault("container_cpus", 2)
    data.setdefault("container_memory", "6g")
    data["task_timeout"] = data.get("task_timeout") or PROFILES[data["model"]]["task_timeout"]
    if not 1 <= data["max_steps"] <= 200:
        raise ValueError("max_steps must be between 1 and 200")
    if not 1 <= data["localization_max_steps"] <= 200:
        raise ValueError("localization_max_steps must be between 1 and 200")
    if data.get("localization_timeout") is not None and data["localization_timeout"] <= 0:
        raise ValueError("localization_timeout must be positive when set")
    if not isinstance(data["concurrency"], int) or data["concurrency"] < 1:
        raise ValueError("concurrency must be positive")
    if not isinstance(data["samples"], int) or data["samples"] < 2:
        raise ValueError("TTS samples must be at least two")
    if data["task_timeout"] <= 0:
        raise ValueError("task_timeout must be positive")
    if data["stage"] not in ("all", "localization", "patch"):
        raise ValueError("stage must be all, localization, or patch")
    if mode != "our_method" and (data.get("locations") or data["stage"] != "all"):
        raise ValueError("locations/stage only apply to our_method")
    for key in ("instances", "output", "runtime", "locations"):
        if data.get(key):
            data[key] = str(Path(data[key]).expanduser().resolve())
    data["method"] = mode
    return data


def localization_config(settings):
    """Match the frozen complete-strategy locations used by the scored P0 run.

    Localization predates the model-specific patch profiles. Applying the patch
    profile here changes the experiment, especially GLM's output allowance.
    """
    data = copy.deepcopy(yaml.safe_load((ROOT / "config/locate.yaml").read_text()))
    data["agent"]["max_steps"] = settings["localization_max_steps"]
    data["localization_runtime"] = {
        "max_tokens": 32768, "api_timeout": 600,
        "tool_choice": "auto", "enable_thinking": False,
        "task_timeout": settings.get("localization_timeout"), "action_repeat_limit": 10,
        "max_compacted_tokens": 110000, "context_compaction_threshold": 100000,
        "context_compaction_target": 90000, "preserve_thinking": False,
    }
    return data
