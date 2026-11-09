"""Verify the published 200-call GLM entry point and its stage handoffs offline."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from phoenix import summary
from phoenix.pipeline import run_task
from phoenix.settings import ROOT, load_settings, write_json
from phoenix.worker import build_config, prepare_tools


@pytest.mark.parametrize("configuration", [
    "configs/our_method.yaml", "configs/models/glm-5.2/our_method.yaml",
    "configs/experiments/glm-5.2-p0-200calls-32k.yaml",
])
def test_current_glm_cli_and_rendered_worker(tmp_path, task, candidate, configuration):
    manifest = tmp_path / "instances.json"
    write_json(manifest, [task])
    result = subprocess.run([sys.executable, "-m", "phoenix.cli", "our_method",
        "--config", str(ROOT / configuration), "--instances", str(manifest), "--dry-run"],
        cwd=ROOT, capture_output=True, text=True,
        env=dict(os.environ, LITELLM_LOCAL_MODEL_COST_MAP="True"), timeout=30)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    settings = plan["settings"]
    assert plan["p0_shared_call_limit"] == 200
    assert plan["model_profile"]["max_tokens"] == 32768
    assert settings["concurrency"] == 4
    settings.update(runtime=str(tmp_path / "runtime"), output=str(tmp_path / "output"))
    request = dict(task=task, settings=settings, stage_dir=str(tmp_path / "stage"),
        base_agent=prepare_tools(tmp_path)["agent"], port=54321, run_id="fixture", job_id="one",
        candidate=candidate)
    config, _ = build_config(request)
    model = config["agent"]["model"]
    assert model["completion_kwargs"]["max_tokens"] == 32768
    assert model["retry"]["retries"] == 8
    assert model["completion_kwargs"]["num_retries"] == 0
    assert model["max_input_tokens"] == 131072
    prompt = config["agent"]["templates"]["instance_template"]
    assert "All 200 model calls are shared" in prompt
    assert "Reserve 40 calls" not in prompt


@pytest.mark.parametrize("upstream,final_allowance", [([], 200), ([50, 50], 99), ([3, 3], 193), ([34, 33, 33], 99)])
def test_full_budget_includes_summary_and_transfers_savings(tmp_path, task, candidate, settings, upstream, final_allowance):
    settings.update(p0_call_budget=200, max_output_tokens=32768)
    candidates = []
    for i in range(len(upstream) or 1):
        value = copy.deepcopy(candidate)
        value["id"] = f"C{i + 1}"
        candidates.append(value)
    charged, summaries = [], []

    class Backend:
        def generate(self, task, stage, **kwargs):
            actual = kwargs["allowance"] if kwargs["final_stage"] else upstream[len(charged)]
            assert actual <= kwargs["allowance"]
            if kwargs["final_stage"]:
                assert kwargs["allowance"] == final_allowance
            charged.append(actual)
            stage.mkdir(parents=True, exist_ok=True)
            patch = stage / "final.patch"
            patch.write_text("diff --git a/app.py b/app.py\n+value = 2\n")
            return dict(patch=str(patch), model_calls=actual, steps=actual, exit_status="submitted")

    def summarize(task, results, config, stage, key, **kwargs):
        assert kwargs["shared_budget"]
        summaries.append(1)
        return "Implement the complete issue."

    report = run_task(task, settings, Backend(), "synthetic-key", candidates, summary_fn=summarize)
    assert report["status"] == "completed"
    assert report["model_calls"] == sum(charged) + sum(summaries) == 200
    assert report["review_calls"] == report["repair_calls"] == 0


def test_current_p0_summary_uses_same_output_and_budget_guidance(tmp_path, monkeypatch, task, settings):
    settings.update(p0_call_budget=200, max_output_tokens=32768)
    requests = []

    class Session:
        def post(self, url, **kwargs):
            requests.append(kwargs)
            class Response:
                ok = True
                def json(self):
                    return dict(choices=[dict(message=dict(content="Check both candidate implementations."), finish_reason="stop")],
                                usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15))
            return Response()
        def close(self):
            pass

    monkeypatch.setattr(summary.requests, "Session", Session)
    patch = tmp_path / "candidate.patch"
    patch.write_text("diff --git a/app.py b/app.py\n+value = 2\n")
    summary.summarize(task, [dict(patch=str(patch), steps=2, exit_status="submitted")],
        settings, tmp_path / "summary", "synthetic-key", shared_budget=True)
    assert len(requests) == 1
    payload = requests[0]["json"]
    assert payload["max_tokens"] == 32768 and payload["temperature"] == 0
    assert "remaining shared generation budget" in payload["messages"][0]["content"]
    assert "Independent review follows" not in payload["messages"][0]["content"]


@pytest.mark.parametrize("overrides", [{"p0_call_budget": 201}, {"p0_call_budget": True},
                                      {"max_output_tokens": 0}, {"max_output_tokens": True}])
def test_invalid_new_limits_fail_before_requests(tmp_path, overrides):
    config = tmp_path / "config.yaml"
    config.write_text("model: glm-5.2\n")
    with pytest.raises(ValueError):
        load_settings("our_method", config, overrides)


def test_explicit_intermediate_budget_reaches_author_prompt(tmp_path, task, candidate, settings):
    settings.update(p0_call_budget=180)
    request = dict(task=task, settings=settings, stage_dir=str(tmp_path / "stage"),
        base_agent=prepare_tools(tmp_path)["agent"], port=54321, run_id="fixture", job_id="one",
        candidate=candidate)
    config, _ = build_config(request)
    prompt = config["agent"]["templates"]["instance_template"]
    assert "All 180 model calls are shared" in prompt and "Reserve 40 calls" not in prompt
