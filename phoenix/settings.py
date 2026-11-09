"""Shared, explicit profiles for all three methods."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
FIELDS = ("instance_id", "problem_statement", "base_commit", "repo_name", "image_name")
CONTEXT_DEFAULTS = dict(max_input_tokens=131072, context_compaction_threshold=120000,
                        context_compaction_target=110000)
PROFILES = {
    "glm-5.2": dict(max_tokens=32768, timeout=240, tool_choice="required", thinking=False, task_timeout=7200),
    "qwen3.7-plus": dict(max_tokens=32768, timeout=600, tool_choice="auto", thinking=False, task_timeout=7200),
    "kimi-k2.7-code": dict(max_tokens=32768, timeout=240, tool_choice="auto", thinking=True, task_timeout=7200),
}
# Transport defaults are shared; generation/context budgets depend on the method.
PROFILES["deepseek-v4.1-flash"] = dict(PROFILES["glm-5.2"])
MODEL_ALIASES = {
    "glm": "glm-5.2", "glm52": "glm-5.2", "glm5.2": "glm-5.2",
    "qwen": "qwen3.7-plus", "qwen3.7": "qwen3.7-plus",
    "kimi": "kimi-k2.7-code", "kimi2.7": "kimi-k2.7-code",
    "flash": "deepseek-v4.1-flash",
}


def normalize_model(value):
    value = value.strip().lower()
    model = MODEL_ALIASES.get(value, value)
    if model not in PROFILES:
        raise ValueError("Unknown model profile: " + value)
    return model


def method_profile(model, method):
    """Return independent resolved defaults for one model/method experiment."""
    model = normalize_model(model)
    if method not in ("single", "tts", "our_method"):
        raise ValueError("Unknown method: " + method)
    if method == "our_method" and model == "kimi-k2.7-code":
        raise ValueError("Kimi P0 has no configured experiment; use single or tts")
    profile = dict(PROFILES[model], **CONTEXT_DEFAULTS, p0_reserved_calls=0,
                   api_retries=8 if method == "our_method" else 20)
    if model == "glm-5.2" and method != "our_method":
        profile["max_tokens"] = 16384
        profile.update(max_input_tokens=1048576, context_compaction_threshold=1030000,
                       context_compaction_target=1000000)
    if model == "kimi-k2.7-code":
        profile.update(max_input_tokens=229376, context_compaction_threshold=220000,
                       context_compaction_target=200000)
    if model == "qwen3.7-plus" and method == "our_method":
        profile.update(p0_reserved_calls=40, api_retries=1)
    profile["summary_max_tokens"] = (profile["max_tokens"] if method == "our_method"
                                     else 32768 if model == "kimi-k2.7-code" else 8192)
    return profile


def summary_profile(settings):
    """Resolved summary request fields, shared by dry-run and actual requests."""
    profile = method_profile(settings["model"], settings["method"])
    p0 = settings["method"] == "our_method"
    kimi = settings["model"] == "kimi-k2.7-code"
    return dict(max_tokens=settings.get("summary_max_tokens", profile["summary_max_tokens"]),
                temperature=0.0 if p0 else 0.7, top_p=1.0,
                enable_thinking=kimi, preserve_thinking=kimi,
                timeout=(30, profile["timeout"]) if p0 else (240 if kimi else 120),
                transport_attempts=1 if p0 else (3 if kimi else 5),
                format_rounds=1 if p0 else 3)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
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


def model_config(model, api_base, *, p0=False, context_settings=None):
    model = normalize_model(model)
    settings = context_settings or {}
    profile = method_profile(model, settings.get("method", "our_method" if p0 else "single"))
    data = {
        "name": "openai/" + model, "api_base": api_base, "api_key": "$PHOENIX_API_KEY",
        "temperature": 0.0, "top_p": 1.0, "per_instance_cost_limit": 0,
        "total_cost_limit": 0, "per_instance_call_limit": 0, "max_input_tokens": 131072,
        "context_compaction_threshold": 120000, "context_compaction_target": 110000,
        "context_keep_first_rounds": 5, "context_keep_last_rounds": 5,
        "retry": {"retries": settings.get("api_retries", profile["api_retries"]), "min_wait": 10, "max_wait": 120},
        "completion_kwargs": {
            "extra_body": {"enable_thinking": profile["thinking"]},
            "max_tokens": settings.get("max_tokens", profile["max_tokens"]), "timeout": profile["timeout"],
            "tool_choice": profile["tool_choice"], "num_retries": 0,
        },
    }
    if profile["thinking"]:
        data["preserve_thinking"] = True
        data["completion_kwargs"]["extra_body"]["preserve_thinking"] = True
    if p0:
        data["request_min_interval"] = 2
    data.update({k: settings.get(k, profile[k]) for k in CONTEXT_DEFAULTS})
    return data


def load_settings(mode, config_path=None, overrides=None):
    overrides = {k: v for k, v in (overrides or {}).items() if v is not None}
    if "model" in overrides:
        overrides["model"] = normalize_model(overrides["model"])
    if config_path is None:
        selected_model = overrides.get("model", "glm-5.2")
        method_profile(selected_model, mode)  # Reject unsupported combinations before file access.
        config_path = ROOT / "configs/models" / selected_model / (mode + ".yaml")
        # Flash supports its own P0 profile and the generic model defaults for sampling.
        data = (yaml.safe_load(config_path.read_text()) or {}) if config_path.exists() else {"model": selected_model}
    else:
        data = yaml.safe_load(Path(config_path).read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a YAML mapping")
    if data.get("model"):
        data["model"] = normalize_model(data["model"])
        if overrides.get("model") and data["model"] != overrides["model"]:
            raise ValueError("--model conflicts with the model in --config; select a matching configuration")
    allowed = {"model", "instances", "output", "runtime", "api_base", "concurrency", "samples", "max_steps",
               "task_timeout", "locations", "stage", "docker_host", "container_cpus", "container_memory",
               "localization_max_steps", "localization_timeout", "adaptive_io", "io_device",
               "environment_start_concurrency", "local_command_concurrency", "pin_cpus",
               "submission_recovery_timeout", "max_tokens", "summary_max_tokens", "p0_reserved_calls", "api_retries", *CONTEXT_DEFAULTS}
    if set(data) - allowed:
        raise ValueError("Unknown configuration fields: " + ", ".join(sorted(set(data) - allowed)))
    data.update(overrides)
    data["model"] = normalize_model(data.get("model", "glm-5.2"))
    profile = method_profile(data["model"], mode)
    for field in (*CONTEXT_DEFAULTS, "max_tokens", "summary_max_tokens", "p0_reserved_calls", "api_retries"):
        if data.get(field) is None:
            data[field] = profile[field]
    for key, env in [("instances", "INSTANCES"), ("runtime", "PHOENIX_RUNTIME"), ("docker_host", "DOCKER_HOST"), ("api_base", "API_BASE")]:
        if not data.get(key) and os.environ.get(env):
            data[key] = os.environ[env]
    data.setdefault("api_base", API_BASE)
    data["api_base"] = data["api_base"] or API_BASE
    data.setdefault("output", "runs/" + data["model"] + "-" + mode)
    data.setdefault("concurrency", 4)
    data.setdefault("samples", 3)
    data.setdefault("max_steps", 200)
    data.setdefault("localization_max_steps", 150)
    data.setdefault("stage", "all")
    data.setdefault("container_cpus", 2)
    data.setdefault("container_memory", "6g")
    data.setdefault("adaptive_io", False)
    data.setdefault("io_device", None)
    data.setdefault("pin_cpus", False)
    data.setdefault("environment_start_concurrency", 2)
    data.setdefault("local_command_concurrency", 4)
    data.setdefault("submission_recovery_timeout", 600)
    data["task_timeout"] = data.get("task_timeout") or profile["task_timeout"]
    context = {k: data.get(k, v) for k, v in CONTEXT_DEFAULTS.items()}
    for key, value in context.items():
        minimum = 0 if key == "context_compaction_threshold" else 1
        if type(value) is not int or value < minimum:
            raise ValueError(key + " must be an integer >= " + str(minimum))
    if context["context_compaction_threshold"]:
        if not (context["context_compaction_target"] <= context["context_compaction_threshold"]
                < context["max_input_tokens"]):
            raise ValueError("Require compaction target <= threshold < max_input_tokens")
    if not 1 <= data["max_steps"] <= 200:
        raise ValueError("max_steps must be between 1 and 200")
    for field in ("max_tokens", "summary_max_tokens", "api_retries"):
        if type(data[field]) is not int or data[field] < 1:
            raise ValueError(field + " must be a positive integer")
    if (type(data["p0_reserved_calls"]) is not int or data["p0_reserved_calls"] < 0 or
            data["p0_reserved_calls"] >= data["max_steps"]):
        raise ValueError("p0_reserved_calls must be nonnegative and less than max_steps")
    if mode != "our_method" and data["p0_reserved_calls"]:
        raise ValueError("p0_reserved_calls only applies to our_method")
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
    if data["adaptive_io"] and not data["io_device"]:
        raise ValueError("adaptive_io requires io_device for the experiment disk")
    for key in ('adaptive_io', 'pin_cpus'):
        if type(data[key]) is not bool:
            raise ValueError(key + ' must be a boolean')
    if data['adaptive_io']:
        component(data['io_device'], 'io_device')
        if not (Path('/sys/class/block') / data['io_device']).exists():
            raise ValueError('io_device must name an existing Linux block device')
    for key in ("environment_start_concurrency", "local_command_concurrency", "submission_recovery_timeout"):
        if type(data[key]) is not int or data[key] < 1:
            raise ValueError(key + " must be a positive integer")
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
