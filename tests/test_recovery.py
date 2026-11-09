"""Fault injection for lifecycle recovery; no model requests or benchmark data."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from phoenix import backend, localization, summary, worker
from phoenix.backend import ControllerInterrupted, DockerBackend
from phoenix.recovery_profiles import recovery_profile
from phoenix.run_state import digest, read
from phoenix.settings import localization_config, write_json


PATCH = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"


@pytest.fixture
def fake_backend(monkeypatch, settings):
    from phoenix import runtime
    monkeypatch.setattr(runtime, "validate", lambda _: None)
    monkeypatch.setattr(worker, "prepare_tools", lambda _: {"agent": {"templates": {}, "tools": {}}})
    launches, cleaned = [], []
    def popen(*args, **kwargs):
        launches.append(args)
        request = read(Path(args[0][-1]))
        write_json(Path(request['stage_dir']) / 'process.json', {'pid': 987654321, 'start_ticks': 'synthetic'})
        return SimpleNamespace(pid=987654321)
    monkeypatch.setattr(backend.subprocess, "Popen", popen)
    monkeypatch.setattr(backend, "process_identity", lambda _: {"start_ticks": "synthetic", "pgid": 987654321})
    monkeypatch.setattr(backend, "find_worker", lambda _: None)
    instance = DockerBackend(settings, "synthetic-unused-key")
    instance.image_id = lambda _: "sha256:synthetic"
    instance.cleanup = cleaned.append
    instance._wait_until = lambda _: None
    instance.launches, instance.cleaned = launches, cleaned
    return instance


def outcome(attempt, *, calls, steps=None, valid=True, patch=PATCH, error="submitted"):
    write_json(attempt / "author_budget.json", {"attempted_calls": calls})
    write_json(attempt / "step-state.json", {"steps": calls if steps is None else steps, "execution_seconds": 80})
    write_json(attempt / "checkpoint.json", {"history": [{"role": "user", "content": "original issue"}],
        "trajectory": [{}] * (calls if steps is None else steps), "info": {"exit_status": error}})
    if patch is not None:
        (attempt / "P0.patch").write_text(patch)
        write_json(attempt / "P0.json", {"mechanical_validation": {"valid": valid}})
    write_json(attempt / "worker-status.json", {"status": "completed"})


def api_interrupted_outcome(attempt, *, calls, retryable=True, patch=PATCH):
    outcome(attempt, calls=calls, steps=2, patch=patch, error="exit_api")
    data = read(attempt / "checkpoint.json")
    data["info"]["api_error"] = {"retryable": retryable, "error_type": "Timeout", "attempts": 3}
    write_json(attempt / "checkpoint.json", data)


def test_api_interruption_resumes_generation_even_if_partial_patch_is_mechanically_valid(fake_backend, task, tmp_path):
    attempts = []
    def monitor(request, attempt, child=None):
        attempts.append(request)
        if len(attempts) == 1:
            api_interrupted_outcome(attempt, calls=7)
        else:
            assert request["prior_calls"] == 7 and request["prior_steps"] == 2
            assert request["recovery"]["mode"] == "continue"
            assert request["settings"]["model"] == "glm-5.2"
            assert request["generation_allowance"] == 160
            assert Path(request["recovery"]["patch"]).read_text() == PATCH
            assert request["recovery"]["trajectory_sha256"] == digest(request["recovery"]["trajectory"])
            outcome(attempt, calls=10)
        return 0, False
    fake_backend._monitor = monitor
    result = fake_backend.generate(task, tmp_path / "stage", allowance=160, final_stage=True)
    assert result["model_calls"] == 10 and result["attempts"] == 2
    assert result["exit_status"] == "submitted" and not result["budget_reset"]
    assert result["review_calls"] == result["repair_calls"] == 0


@pytest.mark.parametrize("retryable,allowance", [(False, 160), (True, 3)])
def test_permanent_api_failure_or_spent_budget_cannot_publish_partial_patch(
        fake_backend, task, tmp_path, retryable, allowance):
    def monitor(request, attempt, child=None):
        api_interrupted_outcome(attempt, calls=3, retryable=retryable)
        return 0, False
    fake_backend._monitor = monitor
    stage = tmp_path / "stage"
    with pytest.raises(RuntimeError, match="Generation/recovery failed"):
        fake_backend.generate(task, stage, allowance=allowance, final_stage=True)
    result = read(stage / "result.json")
    assert result["generation_interrupted"] and result["attempts"] == 1
    assert result["model_calls"] == 3 and not result["budget_reset"]
    assert not (stage / "final.patch").exists()
    assert (stage / "attempts/001/P0.patch").read_text() == PATCH


def test_transport_recovery_is_bounded_and_does_not_reset_calls(fake_backend, task, tmp_path):
    def monitor(request, attempt, child=None):
        api_interrupted_outcome(attempt, calls=request["prior_calls"] + 3)
        return 0, False
    fake_backend._monitor = monitor
    stage = tmp_path / "stage"
    with pytest.raises(RuntimeError, match="Generation/recovery failed"):
        fake_backend.generate(task, stage, allowance=160, final_stage=True)
    result = read(stage / "result.json")
    assert result["attempts"] == 2 and result["model_calls"] == 6
    assert result["generation_interrupted"] and not (stage / "final.patch").exists()


def test_failed_api_snapshot_keeps_the_original_container(fake_backend, task, tmp_path, monkeypatch):
    def monitor(request, attempt, child=None):
        api_interrupted_outcome(attempt, calls=3, patch=None)
        return 0, False
    fake_backend._monitor = monitor
    monkeypatch.setattr(backend, "capture_checkout", lambda *a, **kw: {"status": "capture_failed"})
    stage = tmp_path / "stage"
    with pytest.raises(RuntimeError, match="Generation/recovery failed"):
        fake_backend.generate(task, stage, allowance=160, final_stage=True)
    result = read(stage / "result.json")
    assert result["preserved_container_job_id"] and not fake_backend.cleaned
    assert result["attempts"] == 1 and result["generation_interrupted"]


@pytest.mark.parametrize("model", ["glm-5.2", "qwen3.7-plus"])
def test_startup_retry_then_verified_reuse(fake_backend, task, tmp_path, model):
    fake_backend.settings["model"] = model
    attempts = []
    def monitor(request, attempt, child=None):
        attempts.append(request)
        if len(attempts) == 1:
            (attempt / "generation.log").write_text("Runtime startup timed out")
            return 1, False
        outcome(attempt, calls=3)
        return 0, False
    fake_backend._monitor = monitor
    # No container was created in this startup failure.
    original = backend.capture_checkout
    backend.capture_checkout = lambda *a, **kw: {"status": "capture_failed"}
    try:
        stage = tmp_path / "stage"
        result = fake_backend.generate(task, stage, allowance=160, final_stage=True)
        assert result["attempts"] == 2 and result["model_calls"] == 3
        assert result["review_calls"] == 0 and not result["budget_reset"]
        assert attempts[1]["prior_calls"] == 0
        assert fake_backend.generate(task, stage, allowance=160, final_stage=True) == result
        assert len(fake_backend.launches) == 2
        (stage / "final.patch").write_text("tampered")
        with pytest.raises(ValueError, match="patch changed"):
            fake_backend.generate(task, stage, allowance=160, final_stage=True)
    finally:
        backend.capture_checkout = original


def test_completed_stage_cannot_resume_with_changed_context(fake_backend, task, tmp_path):
    def monitor(request, attempt, child=None):
        outcome(attempt, calls=3)
        return 0, False
    fake_backend._monitor = monitor
    stage = tmp_path / "stage"
    fake_backend.generate(task, stage, allowance=160, final_stage=True)
    fake_backend.settings.update(max_input_tokens=1048576, context_compaction_threshold=1030000,
                                 context_compaction_target=1000000)
    with pytest.raises(ValueError, match="configuration mismatch"):
        fake_backend.generate(task, stage, allowance=160, final_stage=True)
    assert len(fake_backend.launches) == 1


def test_continue_keeps_calls_steps_time_and_original_model(fake_backend, task, tmp_path, monkeypatch):
    attempts = []
    def monitor(request, attempt, child=None):
        attempts.append(request)
        if len(attempts) == 1:
            outcome(attempt, calls=100, patch=None, error="exit_environment_error")
            write_json(attempt / "query-state.json", {"reserved_step": 101})
            return 1, False
        assert request["prior_calls"] == 100
        assert request["prior_steps"] == 101 and request["steps_offset"] == 1
        assert request["prior_execution_seconds"] == 80
        assert request["generation_allowance"] == 160
        assert request["recovery"]["mode"] == "continue"
        assert request["settings"]["model"] == "glm-5.2"
        assert request["deadline"] - request["started_at"] < 7200
        outcome(attempt, calls=108, steps=109)
        return 0, False
    def capture(request, env, attempt, **kw):
        (attempt / "captured.patch").write_text(PATCH)
        value = {"status": "captured", "patch_bytes": len(PATCH),
                 "patch_sha256": digest(attempt / "captured.patch"), "patched_tree": "synthetic-tree"}
        write_json(attempt / "capture.json", value)
        return value
    monkeypatch.setattr(backend, "capture_checkout", capture)
    fake_backend._monitor = monitor
    result = fake_backend.generate(task, tmp_path / "stage", allowance=160, final_stage=True)
    assert result["model_calls"] == 108 and result["steps"] == 109 and result["attempts"] == 2


def test_failed_submission_replay_keeps_original_seed_and_never_requeries(fake_backend, task, tmp_path):
    attempts = []
    def monitor(request, attempt, child=None):
        attempts.append(request)
        if len(attempts) == 1:
            outcome(attempt, calls=160, valid=False)
        elif len(attempts) == 2:
            assert request["recovery"]["mode"] == "submission" and request["prior_calls"] == 160
            outcome(attempt, calls=160, valid=False, patch="corrupted partial replay")
        else:
            assert Path(request["recovery"]["patch"]).read_text() == PATCH
            assert request["prior_calls"] == 160
            outcome(attempt, calls=160)
        return 0, False
    fake_backend._monitor = monitor
    result = fake_backend.generate(task, tmp_path / "stage", allowance=160, final_stage=True)
    assert result["attempts"] == 3 and result["model_calls"] == 160
    assert result["repair_calls"] == result["review_calls"] == 0


def test_replay_exhaustion_is_terminal_on_resume(fake_backend, task, tmp_path):
    def monitor(request, attempt, child=None):
        outcome(attempt, calls=160, valid=False)
        return 0, False
    fake_backend._monitor = monitor
    stage = tmp_path / "stage"
    with pytest.raises(RuntimeError, match="Generation/recovery failed"):
        fake_backend.generate(task, stage, allowance=160, final_stage=True)
    assert len(fake_backend.launches) == 3
    with pytest.raises(RuntimeError, match="exhausted"):
        fake_backend.generate(task, stage, allowance=160, final_stage=True)
    assert len(fake_backend.launches) == 3
    assert read(stage / "result.json")["model_calls"] == 160


def test_controller_resume_adopts_worker_instead_of_duplicate_launch(fake_backend, task, tmp_path):
    visits = []
    def monitor(request, attempt, child=None):
        visits.append(child)
        if len(visits) == 1:
            raise ControllerInterrupted()
        assert child is None
        outcome(attempt, calls=7)
        return 0, False
    fake_backend._monitor = monitor
    stage = tmp_path / "stage"
    with pytest.raises(ControllerInterrupted):
        fake_backend.generate(task, stage, allowance=160, final_stage=True)
    assert not fake_backend.cleaned
    result = fake_backend.generate(task, stage, allowance=160, final_stage=True)
    assert len(fake_backend.launches) == 1 and result["attempts"] == 1
    assert result["model_calls"] == 7 and len(fake_backend.cleaned) == 1


@pytest.mark.parametrize("model,reserve", [("glm-5.2", 60), ("qwen3.7-plus", 60), ("kimi-k2.7-code", 0)])
def test_tts_timeout_captures_before_cleanup(fake_backend, task, tmp_path, monkeypatch, model, reserve):
    fake_backend.settings.update(method="tts", model=model, task_timeout=3000 if model.startswith("kimi") else 7200)
    fake_backend.profile = recovery_profile(fake_backend.settings)
    events = []
    def monitor(request, attempt, child=None):
        assert request["deadline"] - request["started_at"] == fake_backend.settings["task_timeout"] - reserve
        outcome(attempt, calls=5, patch=None, error=None)
        return -15, True
    def capture(request, env, attempt, **kw):
        events.append("capture")
        assert kw["official"] == (model != "kimi-k2.7-code")
        (attempt / "captured.patch").write_text(PATCH)
        return {"status": "captured", "patch_bytes": len(PATCH)}
    fake_backend._monitor = monitor
    fake_backend.cleanup = lambda _: events.append("cleanup")
    monkeypatch.setattr(backend, "capture_checkout", capture)
    result = fake_backend.generate(task, tmp_path / "stage", allowance=200, final_stage=True)
    assert events == ["capture", "cleanup"]
    assert result["exit_status"] == "wall_timeout_partial" and result["model_calls"] == 5


def test_capture_survives_controller_crash_after_container_cleanup(fake_backend, task, tmp_path, monkeypatch):
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "captured.patch").write_text(PATCH)
    write_json(attempt / "capture.json", {"status": "captured", "patch_sha256": digest(attempt / "captured.patch")})
    monkeypatch.setattr(backend, "capture_checkout", lambda *a, **kw: pytest.fail("must reuse saved capture"))
    observed = fake_backend._read_attempt({"prior_calls": 9, "prior_steps": 8}, attempt, None, True)
    assert observed["patch"].read_text() == PATCH and observed["calls"] == 9


def test_summary_resume_consumes_saved_response_once(tmp_path, monkeypatch, task, settings):
    settings["method"] = "tts"
    candidate = tmp_path / "candidate.patch"
    candidate.write_text(PATCH)
    candidates = [dict(patch=str(candidate), steps=2, exit_status="submitted")]
    calls = []
    answer = "BEST_SAMPLE: 1\nREASONING:\n" + "Covers the issue. " * 12 + "\nINSIGHTS FOR NEXT ATTEMPT:\nCheck callers."
    class Session:
        def post(self, *a, **kw):
            calls.append(kw)
            return SimpleNamespace(ok=True, json=lambda: {"choices": [{"message": {"content": answer}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})
        def close(self): pass
    monkeypatch.setattr(summary.requests, "Session", Session)
    directory = tmp_path / "summary"
    original = summary.write_json
    def crash(path, value):
        original(path, value)
        if path.name == "response-1.json":
            raise KeyboardInterrupt
    monkeypatch.setattr(summary, "write_json", crash)
    with pytest.raises(KeyboardInterrupt):
        summary.summarize(task, candidates, settings, directory, "synthetic-key")
    monkeypatch.setattr(summary, "write_json", original)
    assert summary.summarize(task, candidates, settings, directory, "synthetic-key") == answer
    assert summary.summarize(task, candidates, settings, directory, "synthetic-key") == answer
    assert len(calls) == 1 and read(directory / "usage.json")["total_tokens"] == 120


def test_p0_unknown_summary_request_is_not_refunded(tmp_path, monkeypatch, task, settings):
    candidate = tmp_path / "candidate.patch"
    candidate.write_text(PATCH)
    candidates = [dict(patch=str(candidate), steps=2, exit_status="submitted")]
    calls = []
    class Session:
        def post(self, *a, **kw):
            calls.append(kw)
            raise KeyboardInterrupt
        def close(self): pass
    monkeypatch.setattr(summary.requests, "Session", Session)
    import sweagent.agent.request_pacing as pacing
    monkeypatch.setattr(pacing, "wait_for_request", lambda *a: None)
    directory = tmp_path / "summary"
    with pytest.raises(KeyboardInterrupt):
        summary.summarize(task, candidates, settings, directory, "synthetic-key", shared_budget=True)
    with pytest.raises(RuntimeError, match="bounded attempts"):
        summary.summarize(task, candidates, settings, directory, "synthetic-key", shared_budget=True)
    assert len(calls) == 1
    assert read(directory / "usage.json")["unknown_requests"] == [1]


@pytest.mark.parametrize("model", ["glm-5.2", "qwen3.7-plus"])
def test_localization_resume_replays_only_uncommitted_tool_and_no_extra_requests(tmp_path, monkeypatch, task, candidate, settings, model):
    source = tmp_path / "source"
    (source / "repo").mkdir(parents=True)
    (source / "repo/app.py").write_text("value = 1\n")
    write_json(source / "source.json", {"base_commit": task["base_commit"]})
    candidate["hypothesis_id"] = "H1"
    actions = iter([
        ("location_read", {"request": {"operation": "read", "path": "app.py"}}),
        ("location_checkpoint", {"request": dict(notes="Read the constant and requirement.",
            requirements=candidate["requirements"], hypotheses=[dict(id="H1", idea=candidate["hypothesis"],
            grounding=["The fixture contains only one constant."])], active_hypothesis="H1",
            single_hypothesis_reason="The issue explicitly specifies the value.")}),
        ("location_candidate", {"request": {"candidate": candidate}}), ("analysis_complete", {}),
    ])
    calls = []
    def api(*args, **kwargs):
        name, arguments = next(actions)
        calls.append(name)
        return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [dict(
            id="call_" + name, type="function", function=dict(name=name, arguments=json.dumps(arguments)))]}}]}
    monkeypatch.setattr(localization, "call_api", api)
    original = localization.write
    def crash(path, value):
        if path.name == "checkpoint.json" and value["step"] == 2:
            raise KeyboardInterrupt  # The store mutated, but the turn did not commit.
        original(path, value)
    monkeypatch.setattr(localization, "write", crash)
    profile = {"model": model, "account": "fixture"}
    config = localization_config(settings)
    arguments = (task, source, tmp_path / "locations", profile, "synthetic-key", config, settings["api_base"])
    with pytest.raises(KeyboardInterrupt):
        localization.run_task(*arguments)
    monkeypatch.setattr(localization, "write", original)
    result = localization.run_task(*arguments)
    assert result["status"] == "localized", result
    assert len(calls) == 4 and result["usage"]["api_calls"] == 4
    assert result["steps"] == 4 and result["tests_run"] is result["patch_generation_run"] is False
    assert localization.run_task(*arguments) == result and len(calls) == 4
    transcript = tmp_path / "locations" / model / task["instance_id"] / "trajectory.jsonl"
    assert [json.loads(line)["step"] for line in transcript.read_text().splitlines()] == [1, 2, 3, 4]


def test_localization_transport_resume_preserves_five_attempt_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(localization.time, "sleep", lambda _: None)
    class Session:
        def post(self, *a, **kw):
            raise requests.Timeout()
    with pytest.raises(requests.Timeout):
        localization.call_api(Session(), "https://example.invalid", "synthetic-key", {"model": "glm-5.2"}, tmp_path)
    assert read(tmp_path / "transport.json")["attempts"] == 5
    class NoRequests:
        def post(self, *a, **kw): pytest.fail("transport budget reset")
    with pytest.raises(RuntimeError, match="exhausted"):
        localization.call_api(NoRequests(), "https://example.invalid", "synthetic-key", {"model": "glm-5.2"}, tmp_path)


def test_stop_owned_skips_unrelated_processes_and_rechecks_pid_identity(monkeypatch):
    from phoenix import run_state

    alive = iter([True, False])
    monkeypatch.setattr(run_state, "owned_alive", lambda record: next(alive))
    monkeypatch.setattr(Path, "glob", lambda self, pattern: [Path('/proc') / str(pid) for pid in (10, 100, 101, 102, 103)])
    identities = {
        10: iter([dict(pgid=10, state='S', start_ticks='kernel')]),
        100: iter([dict(pgid=100, state='S', start_ticks='owner')] * 2),
        101: iter([dict(pgid=100, state='Z', start_ticks='zombie')]),
        102: iter([None]),
        103: iter([dict(pgid=100, state='S', start_ticks='before'),
                   dict(pgid=100, state='S', start_ticks='reused')]),
    }
    monkeypatch.setattr(run_state, "process_identity", lambda pid: next(identities[pid]))
    opened, closed, signalled = [], [], []

    def pidfd_open(pid):
        if pid == 10:
            raise OSError(22, 'Invalid argument for an unrelated kernel task')
        opened.append(pid)
        return pid + 1000

    monkeypatch.setattr(run_state.os, "pidfd_open", pidfd_open)
    monkeypatch.setattr(run_state.os, "close", closed.append)
    monkeypatch.setattr(run_state.signal, "pidfd_send_signal", lambda handle, sig: signalled.append((handle, sig)))
    run_state.stop_owned(dict(pid=100, start_ticks='owner'), grace=0)
    assert opened == [100, 103]
    assert signalled == [(1100, run_state.signal.SIGTERM), (1100, run_state.signal.SIGKILL)]
    assert closed == [1103, 1100]


def test_stop_owned_rejects_reused_identity_then_stops_only_owned_process():
    from phoenix.run_state import process_identity, stop_owned
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
    try:
        identity = process_identity(child.pid)
        with pytest.raises(RuntimeError, match='identity changed'):
            stop_owned({'pid': child.pid, 'start_ticks': 'wrong'})
        assert child.poll() is None
        stop_owned({'pid': child.pid, 'start_ticks': identity['start_ticks']}, grace=.1)
        assert child.wait(timeout=5) < 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_replay_preserves_binary_content_symlinks_modes_and_refuses_wrong_tree(tmp_path):
    from phoenix.submission_probe import replay_submission
    repo = tmp_path / 'repo'
    repo.mkdir()
    def git(*args):
        return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True).stdout
    git('init', '-q')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Fixture')
    (repo / 'asset.bin').write_bytes(b'\x00original\xff')
    (repo / 'launch.sh').write_text('#!/bin/sh\nexit 0\n')
    git('add', '.')
    git('commit', '-qm', 'base')
    (repo / 'asset.bin').write_bytes(b'\x00recovered\xfe')
    (repo / 'launch.sh').chmod(0o755)
    (repo / 'asset-link').symlink_to('asset.bin')
    git('add', '.')
    patch = tmp_path / 'original.patch'
    patch.write_bytes(git('diff', '--binary', 'HEAD'))
    tree = git('write-tree').decode().strip()
    git('reset', '--hard', 'HEAD')
    result = replay_submission(repo, patch, expected_tree=tree)
    assert result['file_content_and_modes_identical'] and result['model_calls_used'] == 0
    assert (repo / 'asset.bin').read_bytes() == b'\x00recovered\xfe'
    assert os.access(repo / 'launch.sh', os.X_OK) and os.readlink(repo / 'asset-link') == 'asset.bin'
    git('reset', '--hard', 'HEAD')
    with pytest.raises(ValueError, match='content or modes differ'):
        replay_submission(repo, patch, expected_tree='not-the-captured-tree')


def test_model_errors_are_not_misclassified_as_environment_retries():
    from phoenix.run_state import retry_reason
    assert retry_reason('pytest exceeded 30 seconds', 'exit_command_timeout', model_started=True) == (None, 0)
    assert retry_reason('Repeated model action', 'exit_action_loop', model_started=True) == (None, 0)
    assert retry_reason('invalid JSON', 'exit_format', model_started=True) == (None, 0)
    tail = [{'action': 'submit', 'observation': 'execution exceeded 30 seconds'}] * 2
    assert retry_reason('', 'exit_command_timeout', model_started=True, trajectory=tail) == ('submission_timeout', 1)


def test_cli_resume_allows_concurrency_change_but_rejects_budget_change(tmp_path, monkeypatch, task):
    from phoenix import cli, pipeline
    manifest = tmp_path / 'tasks.json'
    write_json(manifest, [task])
    calls = []
    monkeypatch.setenv('PHOENIX_API_KEY', 'synthetic-key')
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))
    def run(instances, settings, key):
        calls.append(settings)
        return [{'status': 'completed', 'instance_id': task['instance_id']}]
    monkeypatch.setattr(pipeline, 'run', run)
    args = ['tts', '--model', 'glm-5.2', '--instances', str(manifest),
            '--output', str(tmp_path / 'output'), '--runtime', str(tmp_path / 'runtime')]
    assert cli.main(args) == 0
    assert cli.main(args + ['--resume', '--concurrency', '8']) == 0
    assert calls[-1]['concurrency'] == 8
    assert cli.main(args + ['--resume', '--max-steps', '199']) == 2
    assert len(calls) == 2
    assert cli.main(args) == 2


def test_resource_gate_obeys_lower_limit_without_terminating_active_command(tmp_path):
    from phoenix.resources import io_slot
    import time
    with io_slot(tmp_path, 'commands', 2):
        write_json(tmp_path / 'resource_policy.json', {'local_command_concurrency': 1})
        with pytest.raises(TimeoutError, match='deadline'):
            with io_slot(tmp_path, 'commands', 2, deadline=time.time() + .05):
                pytest.fail('admitted above reduced limit')
    with io_slot(tmp_path, 'commands', 2, deadline=time.time() + 1):
        pass


def test_recovered_startup_failure_does_not_count_old_calls_as_new_model_work(fake_backend, tmp_path, monkeypatch):
    attempt = tmp_path / 'attempt'
    attempt.mkdir()
    (attempt / 'generation.log').write_text('Runtime startup timed out')
    write_json(attempt / 'author_budget.json', {'attempted_calls': 100})
    monkeypatch.setattr(backend, 'capture_checkout', lambda *a, **kw: {'status': 'capture_failed'})
    observed = fake_backend._read_attempt({'prior_calls': 100, 'prior_steps': 100}, attempt, 1, False)
    assert observed['reason'] == 'container_start_timeout' and observed['retry_limit'] == 3
    assert observed['calls'] == observed['steps'] == 100


def test_crashed_worker_charges_elapsed_generation_time(fake_backend, task, tmp_path, monkeypatch):
    import time
    attempts = []
    def monitor(request, attempt, child=None):
        attempts.append(request)
        if len(attempts) == 1:
            request['started_at'] -= 100
            outcome(attempt, calls=5, patch=None, error='exit_environment_error')
            write_json(attempt / 'worker-status.json', {'status': 'generating', 'at': request['started_at']})
            return 1, False
        assert request['deadline'] - request['started_at'] <= 7100
        assert request['prior_calls'] == 5
        outcome(attempt, calls=8)
        return 0, False
    monkeypatch.setattr(backend, 'capture_checkout', lambda *a, **kw: {'status': 'capture_failed'})
    fake_backend._monitor = monitor
    result = fake_backend.generate(task, tmp_path / 'stage', allowance=160, final_stage=True)
    assert result['active_seconds'] >= 100 and result['model_calls'] == 8
