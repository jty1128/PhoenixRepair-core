import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import requests

from phoenix import summary
from phoenix.settings import ROOT, localization_config, write_json
from phoenix.submission_probe import normalize_test_submission


@pytest.mark.parametrize("script", ["run_single.sh", "run_tts.sh", "our_method.sh", "run_our_method.sh"])
def test_shell_dry_run_without_credentials_or_docker(tmp_path, task, script):
    manifest = tmp_path / "input.json"
    write_json(manifest, [task])
    env = dict(os.environ, PYTHON_BIN=sys.executable, PHOENIX_API_KEY="synthetic-private-value",
               DOCKER_HOST="unix:///not-a-daemon", LITELLM_LOCAL_MODEL_COST_MAP="True")
    output = tmp_path / "not-created"
    run = subprocess.run(["bash", str(ROOT / script), "--instances", str(manifest),
                          "--output", str(output), "--dry-run"], cwd=tmp_path,
                         env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert "synthetic-private-value" not in run.stdout + run.stderr
    plan = json.loads(run.stdout)
    assert not plan["review_calls_enabled"]
    assert not output.exists()


@pytest.mark.parametrize("bad_response", [False, True])
def test_summary_profile_format_and_shared_budget(monkeypatch, tmp_path, task, settings, bad_response):
    calls = []
    answer = "**BEST_SAMPLE:** 1\n**REASONING:** Correct constant.\n**INSIGHTS FOR NEXT ATTEMPT:** Update value."

    class Session:
        def post(self, url, **kwargs):
            calls.append(kwargs)
            if bad_response:
                raise requests.ConnectionError("Synthetic DNS error")
            class Response:
                ok = True
                def json(self):
                    return dict(choices=[dict(message=dict(content=answer), finish_reason="stop")],
                                usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15))
            return Response()
        def close(self):
            pass

    monkeypatch.setattr(summary.requests, "Session", Session)
    patch = tmp_path / "candidate.patch"
    patch.write_text("diff --git a/app.py b/app.py\n+value = 2\n")
    kwargs = dict(task=task, candidates=[dict(patch=str(patch), steps=2, exit_status="submitted")],
                  settings=settings, directory=tmp_path / "summary", key="synthetic-private-value", shared_budget=True)
    if bad_response:
        with pytest.raises(RuntimeError, match="bounded"):
            summary.summarize(**kwargs)
    else:
        assert summary.summarize(**kwargs) == answer
    assert len(calls) == 1
    assert calls[0]["json"]["model"] == settings["model"]
    assert calls[0]["json"]["temperature"] == 0.7
    for path in (tmp_path / "summary").iterdir():
        assert "synthetic-private-value" not in path.read_text()


def test_submission_cleanup_preserves_production_hunks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout
    git("init", "-q")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    (repo / "app.py").write_text("value = 1\n")
    (repo / "test_app.py").write_text("assert 1\n")
    git("add", ".")
    git("commit", "-qm", "Base")
    (repo / "app.py").write_text("value = 2\n")
    (repo / "test_app.py").write_text("assert 2\n")
    git("add", ".")
    production = git("diff", "--binary", "--no-ext-diff", "HEAD", "--", "app.py")
    patch = tmp_path / "submission.patch"
    patch.write_bytes(git("diff", "--binary", "--no-ext-diff", "HEAD"))
    result = normalize_test_submission(repo, patch)
    assert result["patch"].encode() == production
    assert result["production_diff_unchanged"] and result["mechanical_validation"]["valid"]
    assert (repo / "test_app.py").read_text() == "assert 1\n"


def test_localization_malformed_tool_feedback_bounded_and_read_only(monkeypatch, tmp_path, task, settings):
    from phoenix import localization
    settings["max_steps"] = 2
    settings["model"] = "glm-5.2"
    config = localization_config(settings)
    source = tmp_path / "source"
    (source / "repo").mkdir(parents=True)
    (source / "repo/app.py").write_text("value = 1\n")
    calls = []

    def api(session, endpoint, key, payload, folder, **kwargs):
        calls.append((payload, kwargs))
        return dict(choices=[dict(message=dict(role="assistant", content="", tool_calls=[dict(
            id=f"call-{len(calls)}", type="function", function=dict(name="location_read", arguments="{"))]))], usage={})

    monkeypatch.setattr(localization, "call_api", api)
    report = localization.run_task(task, source, tmp_path / "loc", {"model": settings["model"], "account": "fixture"},
                                   "synthetic-key", config, settings["api_base"])
    assert report["status"] == "failed" and report["steps"] == 2
    assert "2-step limit" in report["error"]
    assert calls[0][0]["max_tokens"] == 8192 and calls[0][1]["timeout"] == 240
    names = {tool["function"]["name"] for tool in calls[0][0]["tools"]}
    assert not names.intersection({"bash", "submit", "str_replace_editor"})
    assert any("Localization tool error:" in m.get("content", "") for m in calls[1][0]["messages"])
    assert not report["patch_generation_run"] and not report["tests_run"]
