import copy
import json
from pathlib import Path

import pytest

from phoenix.pipeline import load_location, run_task, task_jobs
from phoenix.settings import load_instances, write_json


class Backend:
    def __init__(self):
        self.calls = []

    def generate(self, task, stage, **kwargs):
        self.calls.append(kwargs)
        stage.mkdir(parents=True, exist_ok=True)
        patch = stage / "final.patch"
        patch.write_text("diff --git a/app.py b/app.py\n+value = 2\n")
        return dict(status="completed", patch=str(patch), steps=3, model_calls=3, exit_status="submitted")


@pytest.mark.parametrize("mode,count", [("single", 1), ("tts", 4), ("our_method", 1)])
def test_pipeline_outputs_only_requested_generation(mode, count, task, candidate, settings):
    settings["method"] = mode
    backend, summaries = Backend(), []

    def summary(task, rows, cfg, stage, key, **kw):
        summaries.append((len(rows), kw))
        return "Toy summary"

    result = run_task(task, settings, backend, "synthetic-key", [candidate], summary_fn=summary)
    assert result["status"] == "completed"
    assert len(backend.calls) == count
    assert result["review_calls"] == result["repair_calls"] == 0
    assert not result["benchmark_evaluated"]
    if mode == "our_method":
        assert backend.calls[0]["allowance"] == 160
        assert backend.calls[0]["candidate"] == candidate
        assert not summaries
        assert result["output_stage"] == "P0"
    elif mode == "tts":
        assert summaries == [(3, {"shared_budget": False})]
        assert backend.calls[-1]["summary"] == "Toy summary"
        assert all(call["candidate"] is None for call in backend.calls)


def test_multicandidate_shared_budget_and_fresh_final(task, candidate, settings):
    second = copy.deepcopy(candidate)
    second["id"] = "C2"
    backend = Backend()
    observed = []

    def summary(task, rows, cfg, stage, key, **kw):
        assert len(rows) == 2 and kw["shared_budget"]
        observed.append(cfg["model"])
        return "Both strategies suggest changing the constant."

    result = run_task(task, settings, backend, "synthetic-key", [candidate, second], summary_fn=summary)
    assert result["status"] == "completed"
    assert [x["allowance"] for x in backend.calls] == [50, 50, 153]
    assert backend.calls[-1]["candidate"] is None
    assert backend.calls[-1]["summary"]
    assert result["model_calls"] == 10  # Three authors x 3, plus one summary.
    assert observed == [settings["model"]]


def test_failed_summary_blocks_final(task, settings):
    settings["method"] = "tts"
    backend = Backend()

    def fail(*a, **kw):
        raise ValueError("Invalid summary")

    result = run_task(task, settings, backend, "synthetic-key", summary_fn=fail)
    assert result["status"] == "failed" and len(backend.calls) == 3


def test_budget_rejects_impossible_candidate_count():
    with pytest.raises(ValueError):
        task_jobs("our_method", max_calls=40)
    with pytest.raises(ValueError):
        task_jobs("our_method", candidates=101)


def test_manifest_never_passes_reference_fields(tmp_path, task):
    file = tmp_path / "tasks.json"
    write_json(file, [{**task, "patch": "REFERENCE", "test_patch": "HIDDEN", "resolved": True}])
    assert load_instances(file) == [task]
    write_json(file, [task, task])
    with pytest.raises(ValueError, match="Duplicate"):
        load_instances(file)
    write_json(file, [{**task, "repo_name": "../other"}])
    with pytest.raises(ValueError):
        load_instances(file)


def test_frozen_location_identity_and_complete_coverage(tmp_path, task, candidate):
    model = "glm-5.2"
    folder = tmp_path / model / task["instance_id"]
    write_json(tmp_path / "instances.json", [task])
    write_json(folder / "report.json", {"status": "localized", "model": model})
    artifact = {"candidates": [candidate]}
    write_json(folder / "location_candidates.json", artifact)
    assert load_location(tmp_path, task, model) == artifact
    with pytest.raises(ValueError, match="identity"):
        load_location(tmp_path, {**task, "problem_statement": "Different request"}, model)
    candidate["requirement_coverage"] = []
    write_json(folder / "location_candidates.json", artifact)
    with pytest.raises(ValueError, match="Incomplete"):
        load_location(tmp_path, task, model)
    write_json(folder / "report.json", {"status": "localized", "model": "qwen3.7-plus"})
    with pytest.raises(ValueError, match="own localization"):
        load_location(tmp_path, task, model)
