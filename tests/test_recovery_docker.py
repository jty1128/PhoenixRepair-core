"""Opt-in real-container fault recovery with synthetic code and no API calls."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

import pytest

from phoenix import worker
from phoenix.capture import capture_checkout, containers
from phoenix.run_state import digest, read
from phoenix.settings import write_json

pytestmark = pytest.mark.skipif(not (os.environ.get("PHOENIX_SMOKE_BASE_IMAGE") and
    os.environ.get("PHOENIX_SMOKE_RUNTIME")), reason="Requires cached smoke image and portable runtime")


def docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=180)


@pytest.fixture(scope="module")
def toy_image():
    image = "phoenixrepair-recovery-smoke:" + uuid.uuid4().hex
    setup = "phoenix-recovery-setup-" + uuid.uuid4().hex
    try:
        docker("create", "--name", setup, "--pull=never", "--network=none", "--user=0", "--entrypoint=/bin/sh",
            os.environ["PHOENIX_SMOKE_BASE_IMAGE"], "-c", "set -eu; mkdir -p /fixture/tests; cd /fixture; "
            "git init -q; git config user.email fixture@example.invalid; git config user.name Fixture; "
            "printf 'value = 1\\n' > app.py; printf 'assert True\\n' > tests/test_app.py; "
            "git add .; git commit -qm base")
        docker("start", "-a", setup)
        docker("commit", "--change", "USER root", "--change", "WORKDIR /fixture", setup, image)
        commit = docker("run", "--rm", "--network=none", "--entrypoint=git", image,
                        "-C", "/fixture", "rev-parse", "HEAD").stdout.strip()
        image_id = docker("image", "inspect", image, "--format", "{{.Id}}").stdout.strip()
        yield image, image_id, commit
    finally:
        docker("rm", "-f", setup, check=False)
        docker("image", "rm", image, check=False)


def request_for(task, settings, tmp_path, toy_image, model):
    image, image_id, commit = toy_image
    task = dict(task, base_commit=commit, image_name=image)
    settings = dict(settings, model=model, method="our_method", runtime=os.environ["PHOENIX_SMOKE_RUNTIME"])
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        port = stream.getsockname()[1]
    return dict(task=task, settings=settings, stage_dir=str(tmp_path / "stage"),
        base_agent=worker.prepare_tools(tmp_path)["agent"], port=port, image_id=image_id,
        run_id="smoke-" + uuid.uuid4().hex, job_id="one", generation_allowance=160, final_stage=True)


def cleanup(request):
    ids = containers(os.environ, request["run_id"], request["job_id"])
    if ids:
        docker("rm", "-f", *ids)


@pytest.mark.parametrize("model", ["glm-5.2", "qwen3.7-plus"])
def test_zero_call_submission_replay_and_test_cleanup(toy_image, tmp_path, task, settings, monkeypatch, model):
    request = request_for(task, settings, tmp_path, toy_image, model)
    seed = tmp_path / "preserved.patch"
    seed.write_text(docker("run", "--rm", "--network=none", "--user=0", "--entrypoint=sh", toy_image[0], "-c",
        "cd /fixture; printf 'value = 2\\n' > app.py; printf 'assert False\\n' > tests/test_app.py; "
        "git add -A; git diff --binary HEAD").stdout)
    request.update(prior_calls=130, prior_steps=117,
        recovery={"mode": "submission", "patch": str(seed), "patch_sha256": digest(seed)})
    from sweagent.agent.models import LiteLLMModel
    monkeypatch.setattr(LiteLLMModel, "query", lambda *a, **kw: pytest.fail("replay must use zero model calls"))
    monkeypatch.setenv("PHOENIX_API_KEY", "synthetic-unused-key")
    try:
        worker.execute(request)
        stage = Path(request["stage_dir"])
        assert "+value = 2" in (stage / "P0.patch").read_text()
        assert "test_app.py" not in (stage / "P0.patch").read_text()
        assert read(stage / "replay.json")["production_diff_unchanged"]
        assert read(stage / "P0.json")["mechanical_validation"]["valid"]
        assert read(stage / "author_budget.json")["attempted_calls"] == 130
        assert read(stage / "replay.json")["model_calls_used"] == 0
        assert "assert False" in seed.read_text()  # Original evidence retained.
    finally:
        cleanup(request)


@pytest.mark.parametrize("model", ["glm-5.2", "qwen3.7-plus", "kimi-k2.7-code"])
def test_timeout_capture_stops_container_commands_before_reading_diff(toy_image, tmp_path, task, settings, model):
    request = request_for(task, settings, tmp_path, toy_image, model)
    stage = Path(request["stage_dir"])
    write_json(stage / "history_isolation.json", {"snapshot_commit": toy_image[2]})
    name = "phoenix-capture-" + uuid.uuid4().hex
    try:
        docker("run", "-d", "--name", name, "--network=none", "--user=0", "--entrypoint=sleep",
            "--label", "phoenixrepair.run=" + request["run_id"], "--label", "phoenixrepair.job=" + request["job_id"],
            "--mount", "type=bind,src=" + os.environ["PHOENIX_SMOKE_RUNTIME"] + "/python,dst=/opt/phoenix-python,readonly",
            toy_image[0], "3600")
        docker("exec", "-d", name, "sh", "-c",
            "printf 'value = 2\\n' > /fixture/app.py; exec sleep 3600")
        for _ in range(20):
            if "value = 2" in docker("exec", name, "cat", "/fixture/app.py").stdout:
                break
            time.sleep(.1)
        result = capture_checkout(request, os.environ.copy(), stage, official=model != "kimi-k2.7-code")
        assert result["status"] == "captured", result
        assert "+value = 2" in (stage / "captured.patch").read_text()
        assert result["model_calls_used"] == 0
        assert docker("exec", name, "cat", "/fixture/app.py").stdout == "value = 2\n"
    finally:
        cleanup(request)


@pytest.mark.parametrize("model", ["glm-5.2", "qwen3.7-plus"])
def test_interrupted_author_restores_history_diff_and_charged_calls(toy_image, tmp_path, task, settings, monkeypatch, model):
    request = request_for(task, settings, tmp_path, toy_image, model)
    request.update(managed_recovery=True)
    stage = Path(request["stage_dir"])
    first = tmp_path / "first.jsonl"
    first.write_text(json.dumps({"toy": [dict(message="Synthetic edit", tool_calls=[dict(id="edit", type="function",
        function=dict(name="str_replace_editor", arguments=json.dumps(dict(command="str_replace",
            path="/fixture/app.py", old_str="value = 1", new_str="value = 2"))))])]}) + "\n")
    second = tmp_path / "second.jsonl"
    second.write_text(json.dumps({"toy": [dict(message="Submit restored work", tool_calls=[dict(id="submit" + str(i),
        type="function", function=dict(name="submit", arguments="{}"))]) for i in range(2)]}) + "\n")
    original_config, original_write = worker.build_config, worker.write_json
    def replay_config(req):
        config, guidance = original_config(req)
        config["agent"]["model"] = {"name": "replay", "replay_path": str(second if req.get("recovery") else first)}
        return config, guidance
    def crash(path, value):
        original_write(path, value)
        if path == stage / "step-state.json":
            raise RuntimeError("synthetic host worker interruption after saved edit")
    monkeypatch.setattr(worker, "build_config", replay_config)
    monkeypatch.setattr(worker, "write_json", crash)
    monkeypatch.setenv("PHOENIX_API_KEY", "synthetic-unused-key")
    try:
        with pytest.raises(RuntimeError, match="synthetic host worker interruption"):
            worker.execute(request)
        monkeypatch.setattr(worker, "write_json", original_write)
        capture = capture_checkout(request, os.environ.copy(), stage)
        assert capture["status"] == "captured", capture
        assert read(stage / "author_budget.json")["attempted_calls"] == 1
        cleanup(request)
        recovered = dict(request, stage_dir=str(tmp_path / "recovered"), job_id="two",
            prior_calls=1, prior_steps=1, steps_offset=0, prior_execution_seconds=80,
            recovery=dict(mode="continue", patch=str(stage / "captured.patch"), patch_sha256=digest(stage / "captured.patch"),
                patched_tree=capture["patched_tree"], trajectory=str(stage / "checkpoint.json"),
                trajectory_sha256=digest(stage / "checkpoint.json")))
        try:
            worker.execute(recovered)
            result = Path(recovered["stage_dir"])
            assert "+value = 2" in (result / "P0.patch").read_text()
            assert read(result / "author_budget.json")["attempted_calls"] == 3
            assert read(result / "step-state.json")["steps"] == 3
            assert read(result / "step-state.json")["execution_seconds"] >= 80
            assert read(result / "P0.json")["mechanical_validation"]["valid"]
        finally:
            cleanup(recovered)
    finally:
        cleanup(request)


def test_worker_deadline_enforced_without_controller(toy_image, tmp_path, task, settings):
    request = request_for(task, settings, tmp_path, toy_image, "glm-5.2")
    request.update(managed_recovery=True, deadline=time.time() + 20, capture_reserve=30)
    replay = tmp_path / "replay.jsonl"
    replay.write_text(json.dumps({"toy": [
        dict(message="Edit", tool_calls=[dict(id="edit", type="function", function=dict(
            name="str_replace_editor", arguments=json.dumps(dict(command="str_replace",
                path="/fixture/app.py", old_str="value = 1", new_str="value = 2"))))]),
        dict(message="Simulate a blocked command", tool_calls=[dict(id="wait", type="function",
            function=dict(name="bash", arguments=json.dumps({"command": "sleep 120"})))]),
    ]}) + "\n")
    path = tmp_path / "request.json"
    write_json(path, request)
    code = ("import sys; from phoenix import worker\n"
            "original = worker.build_config\n"
            "def replay_config(request):\n"
            " config, guidance = original(request)\n"
            " config['agent']['model'] = {'name':'replay','replay_path':sys.argv[2]}\n"
            " return config, guidance\n"
            "worker.build_config = replay_config\n"
            "from phoenix.run_state import lock, read\n"
            "from pathlib import Path\n"
            "with lock(Path(sys.argv[1]).parent / 'worker.lock', blocking=False):\n"
            " worker.run_owned(read(sys.argv[1]))\n")
    child = None
    try:
        with (tmp_path / "watchdog.log").open("w") as log:
            child = subprocess.Popen([sys.executable, "-c", code, str(path), str(replay)],
                env=dict(os.environ, PHOENIX_API_KEY="synthetic-unused-key"),
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            rc = child.wait(timeout=65)
        stage = Path(request["stage_dir"])
        assert rc == -9, (rc, (tmp_path / "watchdog.log").read_text()[-5000:])
        assert read(stage / "termination.json")["reason"] == "wall_timeout"
        assert read(stage / "capture.json")["status"] == "captured"
        assert "+value = 2" in (stage / "captured.patch").read_text()
        assert read(stage / "author_budget.json")["attempted_calls"] == 2
    finally:
        if child and child.poll() is None:
            child.kill()
            child.wait()
        cleanup(request)


@pytest.mark.parametrize('model', ['glm-5.2', 'qwen3.7-plus'])
@pytest.mark.parametrize('method', ['tts', 'our_method'])
def test_real_controller_full_pipeline_and_resume_without_api(toy_image, tmp_path, task, settings, candidate, monkeypatch, model, method):
    from types import SimpleNamespace
    from phoenix import backend, pipeline, summary
    request = request_for(task, settings, tmp_path, toy_image, model)
    settings, task = request['settings'], request['task']
    settings['method'] = method
    # Test-only executable substitutes a deterministic model inside the real
    # subprocess. Production config, controller, Docker and recovery stay real.
    actions = [dict(message='Edit fixture', tool_calls=[dict(id='edit', type='function', function=dict(
        name='str_replace_editor', arguments=json.dumps(dict(command='str_replace', path='/fixture/app.py',
            old_str='value = 1', new_str='value = 2'))))])]
    actions += [dict(message='Submit', tool_calls=[dict(id='submit'+str(i), type='function',
        function=dict(name='submit', arguments='{}'))]) for i in range(2)]
    replay = tmp_path / 'actions.jsonl'
    replay.write_text(json.dumps({'toy': actions}) + '\n')
    launcher = tmp_path / 'offline-worker'
    launcher.write_text('#!' + sys.executable + '\nimport sys, os\nsys.path.insert(0, os.getcwd())\nfrom phoenix import worker\n'
        'original = worker.build_config\n'
        'def replay_config(request):\n'
        ' config, guidance = original(request)\n'
        ' config["agent"]["model"] = {"name":"replay","replay_path":' + repr(str(replay)) + '}\n'
        ' return config, guidance\n'
        'worker.build_config = replay_config\n'
        'sys.argv = ["worker", sys.argv[-1]]\nworker.main()\n')
    launcher.chmod(0o755)
    monkeypatch.setattr(backend, 'sys', SimpleNamespace(executable=str(launcher)))
    requests = []
    answer = 'BEST_SAMPLE: 1\nREASONING:\n' + 'The patch covers the issue and its callers. ' * 4
    answer += '\nINSIGHTS FOR NEXT ATTEMPT:\nCheck the required value.'
    class Session:
        def post(self, *a, **kw):
            requests.append(kw)
            return SimpleNamespace(ok=True, json=lambda: {'choices': [{'message': {'content': answer}}]})
        def close(self): pass
    monkeypatch.setattr(summary.requests, 'Session', Session)
    controller = backend.DockerBackend(settings, 'synthetic-unused-key')
    candidates = [candidate] if method == 'our_method' else None
    result = pipeline.run_task(task, settings, controller, 'synthetic-unused-key', candidates)
    assert result['status'] == 'completed', result
    assert '+value = 2' in Path(result['patch']).read_text()
    assert result['review_calls'] == result['repair_calls'] == 0
    assert result['model_calls'] == (12 if method == 'tts' else 3)
    assert len(requests) == (1 if method == 'tts' else 0)
    # Completed work survives a new controller without creating any container.
    resumed = backend.DockerBackend(settings, 'synthetic-unused-key')
    monkeypatch.setattr(resumed, 'image_id', lambda *a: pytest.fail('completed stages must be reused'))
    assert pipeline.run_task(task, settings, resumed, 'synthetic-unused-key', candidates) == result
    assert len(requests) == (1 if method == 'tts' else 0)
    assert not docker('ps', '-aq', '--filter', 'label=phoenixrepair.run=' + controller.run_id).stdout.strip()
