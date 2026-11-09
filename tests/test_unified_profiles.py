"""Exercise public entrypoints through the settings used by real workers."""
import copy
import json
import os
import subprocess
import sys

import pytest

from phoenix import cli, pipeline
from phoenix.settings import ROOT, load_settings, summary_profile, write_json
from phoenix.worker import build_config, prepare_tools


# Canonical model, CLI alias, method, input limit, output limit, reserved calls.
EXPERIMENTS = [
    ("glm-5.2", "glm5.2", "single", 1048576, 16384, 0),
    ("glm-5.2", "glm5.2", "tts", 1048576, 16384, 0),
    ("glm-5.2", "glm5.2", "our_method", 131072, 32768, 0),
    ("qwen3.7-plus", "qwen", "single", 131072, 32768, 0),
    ("qwen3.7-plus", "qwen", "tts", 131072, 32768, 0),
    ("qwen3.7-plus", "qwen", "our_method", 131072, 32768, 40),
    ("kimi-k2.7-code", "kimi", "single", 229376, 32768, 0),
    ("kimi-k2.7-code", "kimi", "tts", 229376, 32768, 0),
]


def experiment_settings(tmp_path, model, method, **overrides):
    return load_settings(method, None, {
        "model": model, "output": str(tmp_path / "output"),
        "runtime": str(tmp_path / "runtime"), **overrides,
    })


def worker_config(tmp_path, task, settings):
    config, _ = build_config(dict(
        task=task, settings=settings, stage_dir=str(tmp_path / "stage"),
        base_agent=prepare_tools(tmp_path)["agent"], port=54321,
        run_id="offline-test", job_id="profile-test",
    ))
    return config


@pytest.mark.parametrize("model,alias,method,input_limit,output_limit,reserved", EXPERIMENTS)
def test_selected_profile_reaches_real_worker(
    tmp_path, task, model, alias, method, input_limit, output_limit, reserved,
):
    settings = experiment_settings(tmp_path, alias, method)
    assert settings["model"] == model
    assert settings["method"] == method
    assert settings["max_steps"] == 200
    assert settings["task_timeout"] == 7200
    assert settings["p0_reserved_calls"] == reserved
    assert settings["stage"] == "all"
    assert not settings.get("locations")

    config = worker_config(tmp_path, task, settings)
    actual = config["agent"]["model"]
    assert actual["name"] == "openai/" + model
    assert config["agent"]["max_steps"] == 200
    assert actual["max_input_tokens"] == settings["max_input_tokens"] == input_limit
    assert actual["completion_kwargs"]["max_tokens"] == settings["max_tokens"] == output_limit
    threshold, target = ({1048576: (1030000, 1000000),
                          131072: (120000, 110000),
                          229376: (220000, 200000)})[input_limit]
    assert actual["context_compaction_threshold"] == threshold
    assert actual["context_compaction_target"] == target
    assert actual["completion_kwargs"]["extra_body"]["enable_thinking"] == (alias == "kimi")
    if alias == "kimi":
        assert actual["preserve_thinking"] is True
        assert actual["completion_kwargs"]["extra_body"]["preserve_thinking"] is True
    if method == "our_method":
        assert config["agent"]["tools"]["total_execution_timeout"] == 7200
        assert actual["retry"]["retries"] == (1 if alias == "qwen" else 8)
    else:
        assert actual["retry"]["retries"] == 20
    if method == "tts":
        assert summary_profile(settings)["max_tokens"] == (32768 if alias == "kimi" else 8192)


@pytest.mark.parametrize("model,alias,method,input_limit,output_limit,reserved", EXPERIMENTS)
@pytest.mark.parametrize("use_alias", [False, True], ids=["canonical", "alias"])
def test_bash_entrypoints_preserve_entire_manifest(
    tmp_path, task, model, alias, method, input_limit, output_limit, reserved, use_alias,
):
    """A normal launch processes all supplied tasks, including never-rerun IDs."""
    manifest = tmp_path / "all-tasks.json"
    tasks = [{**task, "instance_id": f"task-{i:02d}"} for i in range(51)]
    write_json(manifest, tasks)
    output = tmp_path / "must-not-be-created"
    script = {"single": "run_single.sh", "tts": "run_tts.sh", "our_method": "our_method.sh"}[method]
    env = dict(os.environ, PYTHON_BIN=sys.executable)
    env.pop("PHOENIX_API_KEY", None)
    env.pop("API_KEY", None)
    result = subprocess.run([
        "bash", str(ROOT / script), "--model", alias if use_alias else model,
        "--instances", str(manifest), "--output", str(output),
        "--runtime", str(tmp_path / "missing-runtime"), "--dry-run",
    ], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["tasks"] == 51
    assert plan["instance_ids"] == [task["instance_id"] for task in tasks]
    assert plan["settings"]["model"] == model
    assert plan["settings"]["method"] == method
    assert plan["settings"]["stage"] == "all"
    assert plan["author_model_config"]["max_input_tokens"] == input_limit
    assert plan["author_model_config"]["completion_kwargs"]["max_tokens"] == output_limit
    assert plan["settings"]["p0_reserved_calls"] == reserved
    assert not output.exists()


def test_dry_run_never_starts_external_work(tmp_path, task, monkeypatch, capsys):
    import requests

    def forbidden(*args, **kwargs):
        pytest.fail("dry-run attempted a subprocess, model request, or execution pipeline")

    monkeypatch.setattr(cli.subprocess, "run", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(pipeline, "run", forbidden)
    monkeypatch.delenv("PHOENIX_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    manifest, output = tmp_path / "tasks.json", tmp_path / "output"
    write_json(manifest, [task])
    assert cli.main(["our_method", "--model", "qwen", "--instances", str(manifest),
                     "--output", str(output), "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["settings"]["stage"] == "all"
    assert plan["method_output"] == "P0"
    assert not output.exists()


def test_explicit_config_rejects_model_conflicts(tmp_path, task, capsys):
    config = ROOT / "configs/models/glm-5.2/single.yaml"
    with pytest.raises(ValueError, match="--model conflicts"):
        load_settings("single", config, {"model": "qwen"})
    assert load_settings("single", config, {"model": "glm5.2"})["model"] == "glm-5.2"
    manifest, output = tmp_path / "tasks.json", tmp_path / "output"
    write_json(manifest, [task])
    assert cli.main(["single", "--config", str(config), "--model", "qwen",
                     "--instances", str(manifest), "--output", str(output), "--dry-run"]) == 2
    assert "--model conflicts" in capsys.readouterr().err
    assert not output.exists()


def test_kimi_p0_is_rejected_before_execution(tmp_path):
    with pytest.raises(ValueError, match="Kimi P0"):
        experiment_settings(tmp_path, "kimi", "our_method")


def test_explicit_author_budget_reaches_worker(tmp_path, task):
    settings = experiment_settings(tmp_path, "glm5.2", "tts", max_tokens=12288, summary_max_tokens=4096)
    actual = worker_config(tmp_path, task, settings)
    assert actual["agent"]["model"]["completion_kwargs"]["max_tokens"] == 12288
    assert summary_profile(settings)["max_tokens"] == 4096


def test_tts_and_p0_use_different_budget_units():
    jobs = pipeline.task_jobs("tts", samples=3, max_calls=200)
    assert [(job["stage"], job.get("allowance")) for job in jobs] == [
        ("sample-1", 200), ("sample-2", 200), ("sample-3", 200),
        ("summary", None), ("final-sample", 200),
    ]
    assert pipeline.task_jobs("our_method", max_calls=200, reserved_calls=40)[0]["allowance"] == 160
    jobs = pipeline.task_jobs("our_method", candidates=2, max_calls=200, reserved_calls=40)
    assert [job["allowance"] for job in jobs if "allowance" in job] == [50, 50, 59]
    assert pipeline.task_jobs("our_method", max_calls=200, reserved_calls=0)[0]["allowance"] == 200


class RecordingBackend:
    def __init__(self):
        self.calls = []

    def generate(self, task, stage, **kwargs):
        self.calls.append(kwargs)
        stage.mkdir(parents=True, exist_ok=True)
        patch = stage / "final.patch"
        patch.write_text("diff --git a/app.py b/app.py\n+value = 2\n")
        return dict(status="completed", patch=str(patch), steps=3, model_calls=3, exit_status="submitted")


@pytest.mark.parametrize("model,expected_final", [("glm5.2", 193), ("qwen", 153)])
def test_unused_candidate_calls_transfer_to_final(tmp_path, task, candidate, model, expected_final):
    settings = experiment_settings(tmp_path, model, "our_method")
    second = copy.deepcopy(candidate)
    second["id"] = "C2"
    backend = RecordingBackend()
    summaries = []

    def summary(task, rows, cfg, stage, key, **kwargs):
        summaries.append(kwargs)
        return "Implement and test the requested constant."

    result = pipeline.run_task(task, settings, backend, "synthetic-key", [candidate, second], summary_fn=summary)
    assert result["status"] == "completed", result
    assert [call["allowance"] for call in backend.calls] == [50, 50, expected_final]
    assert summaries == [{"shared_budget": True}]
    assert backend.calls[-1]["candidate"] is None
    assert backend.calls[-1]["summary"] == "Implement and test the requested constant."
    assert result["model_calls"] == 10
    assert result["review_calls"] == result["repair_calls"] == 0
    assert result["output_stage"] == "P0"
