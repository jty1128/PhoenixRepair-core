from pathlib import Path

import pytest

from phoenix import pipeline, runtime, summary, worker
from phoenix.summary import validate_tts_summary


@pytest.mark.parametrize("heading", ["BEST_SAMPLE: 2", "**BEST_SAMPLE:** 2", "__BEST_SAMPLE:__ [2]", "best_sample: [2]"])
def test_historical_tts_summary_formats(heading):
    answer = heading + "\nREASONING:\n" + ("Compare all candidate mechanisms against the issue. " * 3)
    answer += "\nINSIGHTS FOR NEXT ATTEMPT:\nKeep compatible behavior and verify the affected caller."
    assert validate_tts_summary(answer) == 2


@pytest.mark.parametrize("answer", [
    "BEST_SAMPLE: 2\nREASONING: OK\nINSIGHTS FOR NEXT ATTEMPT: OK",
    "BEST_SAMPLE: 4\nREASONING:\n" + "Details. " * 30 + "\nINSIGHTS FOR NEXT ATTEMPT: Inspect callers.",
    "BEST_SAMPLE: 1\nREASONING:\n" + "Details. " * 30,
])
def test_historical_tts_summary_rejects_incomplete_outputs(answer):
    with pytest.raises(ValueError):
        validate_tts_summary(answer)


@pytest.mark.parametrize("model", ["glm-5.2", "qwen3.7-plus"])
def test_tts_runs_three_independent_samples_then_summary_and_fresh_final(monkeypatch, tmp_path, task, settings, model):
    settings.update(method="tts", model=model)
    generated, requests = [], []
    answer = "BEST_SAMPLE: [2]\nREASONING:\n" + ("Candidate two covers the requested behavior and its callers. " * 3)
    answer += "\nINSIGHTS FOR NEXT ATTEMPT:\nUse the correct behavior and check edge cases."

    class Session:
        def post(self, url, **kwargs):
            requests.append(kwargs)
            class Response:
                ok = True
                def json(self):
                    return dict(choices=[dict(message=dict(content=answer), finish_reason="stop")])
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(summary.requests, "Session", Session)

    class Backend:
        def generate(self, task, stage, **kwargs):
            assert kwargs["candidate"] is None  # TTS never receives a localization plan.
            assert kwargs["allowance"] == 200
            generated.append((stage.name, kwargs))
            if kwargs["final_stage"]:
                assert kwargs["summary"] == answer and kwargs["num_candidates"] == 3
            else:
                assert kwargs["summary"] is None
            stage.mkdir(parents=True)
            patch = stage / "final.patch"
            patch.write_text(f"diff --git a/app.py b/app.py\n+value = {len(generated)}\n")
            return dict(patch=str(patch), steps=3, model_calls=3, exit_status="submitted")

    result = pipeline.run_task(task, settings, Backend(), "synthetic-key")
    assert result["status"] == "completed"
    assert [stage for stage, _ in generated] == ["sample-1", "sample-2", "sample-3", "final-sample"]
    assert len(requests) == 1
    payload = requests[0]["json"]
    assert payload["model"] == model and payload["temperature"] == 0.7
    assert payload["max_tokens"] == 8192 and not payload["enable_thinking"]
    assert requests[0]["timeout"] == 120
    prompt = payload["messages"][0]["content"]
    assert all(f"+value = {i}" in prompt for i in (1, 2, 3))
    assert "Candidate patches are proposals" not in prompt
    assert "+value = 4" in Path(result["patch"]).read_text()  # Fresh final, not selected sample 2.
    assert result["review_calls"] == result["repair_calls"] == 0


def test_standalone_worker_gets_account_identity_without_exposing_key(monkeypatch, settings):
    monkeypatch.delenv("PHOENIX_ACCOUNT_ID", raising=False)
    monkeypatch.setattr(runtime, "validate", lambda _: None)
    monkeypatch.setattr(worker, "prepare_tools", lambda _: {"agent": {}})
    first = pipeline.DockerBackend(settings, "synthetic-one")
    same = pipeline.DockerBackend(settings, "synthetic-one")
    other = pipeline.DockerBackend(settings, "synthetic-two")
    assert first.env["PHOENIX_ACCOUNT_ID"] == same.env["PHOENIX_ACCOUNT_ID"]
    assert first.env["PHOENIX_ACCOUNT_ID"] != other.env["PHOENIX_ACCOUNT_ID"]
    assert "synthetic-one" not in first.env["PHOENIX_ACCOUNT_ID"]
