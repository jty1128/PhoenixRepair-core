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
        assert backend.calls[0]["allowance"] == 200
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
    assert [x["allowance"] for x in backend.calls] == [50, 50, 193]
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


@pytest.mark.parametrize("model", ["glm-5.2", "qwen3.7-plus"])
@pytest.mark.parametrize("candidate_count", [1, 2])
def test_complete_localization_to_final_p0(monkeypatch, tmp_path, task, candidate, settings, model, candidate_count):
    """Exercise real localization tools and handoff; only API/Docker are faked."""
    from phoenix import pipeline, localization, summary
    settings.update(model=model, stage="all", concurrency=1)
    source = tmp_path / "source"
    (source / "repo").mkdir(parents=True)
    (source / "repo/app.py").write_text("value = 1\n")
    actions = [("location_read", {"request": dict(operation="read", path="app.py")})]
    hypotheses = [dict(id=f"H{i}", idea=f"Complete implementation approach {i}.", grounding=["app.py defines the only value."])
                  for i in range(1, candidate_count + 1)]
    actions.append(("location_checkpoint", {"request": dict(notes="Read app.py; implement the required value.",
        requirements=candidate["requirements"], hypotheses=hypotheses, active_hypothesis="H1",
        single_hypothesis_reason="Only one state-free constant exists in this fixture.")}))
    for i in range(1, candidate_count + 1):
        if i > 1:
            actions.append(("location_checkpoint", {"request": dict(notes="Explore the next independent approach.",
                                                                     active_hypothesis=f"H{i}")}))
        plan = copy.deepcopy(candidate)
        plan.update(id=f"C{i}", hypothesis_id=f"H{i}")
        actions.append(("location_candidate", {"request": dict(candidate=plan)}))
    actions.append(("analysis_complete", {}))
    pending = iter(actions)
    events = []
    def api(session, endpoint, key, payload, directory, **kwargs):
        assert payload["model"] == model
        assert payload["max_tokens"] == 32768 and kwargs["timeout"] == 600
        name, arguments = next(pending)
        events.append(name)
        return dict(choices=[dict(message=dict(role="assistant", content="", tool_calls=[dict(
            id=f"call-{len(events)}", type="function", function=dict(name=name, arguments=json.dumps(arguments)))]))])
    monkeypatch.setattr(localization, "call_api", api)
    monkeypatch.setattr(localization, "export_source", lambda *a: source)
    class FakeSession:
        def post(self, url, **kwargs):
            assert kwargs["json"]["model"] == model and kwargs["json"]["temperature"] == 0
            events.append("summary")
            class Response:
                ok = True
                def json(self):
                    return dict(choices=[dict(message=dict(content="Implement the required constant and verify callers."))])
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(summary.requests, "Session", FakeSession)
    backend = Backend()
    original = backend.generate
    def generate(*args, **kwargs):
        assert "analysis_complete" in events
        events.append("generate")
        return original(*args, **kwargs)
    backend.generate = generate
    monkeypatch.setattr(pipeline, "DockerBackend", lambda *args: backend)
    reports = pipeline.run([task], settings, "synthetic-key")
    assert len(reports) == 1 and reports[0]["status"] == "completed", reports
    assert reports[0]["output_stage"] == "P0" and reports[0]["review_calls"] == reports[0]["repair_calls"] == 0
    assert events[:len(actions)] == [a[0] for a in actions]
    assert events[len(actions):] == (["generate"] if candidate_count == 1 else ["generate", "generate", "summary", "generate"])
    output = Path(settings["output"]) / model / task["instance_id"]
    assert (output / "P0.patch").read_bytes() == (output / "final.patch").read_bytes()
