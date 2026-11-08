"""Opt-in end-to-end test using synthetic code and deterministic replay, no API."""
import json
import os
from pathlib import Path
import socket
import subprocess
import uuid

import pytest

from phoenix.settings import write_json
from phoenix import worker


@pytest.mark.skipif(not (os.environ.get("PHOENIX_SMOKE_BASE_IMAGE") and os.environ.get("PHOENIX_SMOKE_RUNTIME")),
                    reason="Set PHOENIX_SMOKE_BASE_IMAGE (cached image with bash/git) and PHOENIX_SMOKE_RUNTIME")
def test_real_container_edit_and_p0_submission(tmp_path, monkeypatch, task, candidate, settings):
    """Build a disposable toy checkout, edit it, submit P0, then remove our image."""
    image = "phoenixrepair-core-smoke:" + uuid.uuid4().hex
    run_id = "smoke-" + uuid.uuid4().hex
    base = os.environ["PHOENIX_SMOKE_BASE_IMAGE"]
    runtime = os.environ["PHOENIX_SMOKE_RUNTIME"]
    def docker(*args, check=True):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180, check=check)
    try:
        name = run_id + "-setup"
        docker("create", "--name", name, "--pull=never", "--network=none", "--user=0", "--entrypoint=/bin/sh",
               "--label", "phoenixrepair.run=" + run_id, base, "-c",
               "set -eu; mkdir -p /fixture; cd /fixture; git init -q; "
               "git config user.email fixture@example.invalid; git config user.name Fixture; "
               "printf 'value = 1\\n' > app.py; git add app.py; git commit -qm base")
        result = docker("start", "-a", name, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        docker("commit", "--change", "USER root", "--change", "WORKDIR /fixture", name, image)
        docker("rm", name)
        commit = docker("run", "--rm", "--network=none", "--entrypoint=git", image,
                        "-C", "/fixture", "rev-parse", "HEAD").stdout.strip()
        task.update(base_commit=commit, image_name=image)
        settings.update(runtime=runtime, method="our_method")
        actions = []
        for name, arguments in [
            ("str_replace_editor", dict(command="str_replace", path="/fixture/app.py", old_str="value = 1", new_str="value = 2")),
            ("submit", {}), ("submit", {}),
        ]:
            actions.append(dict(message="Synthetic offline action.", tool_calls=[dict(id="call_" + str(len(actions)),
                type="function", function=dict(name=name, arguments=json.dumps(arguments)))]))
        replay = tmp_path / "replay.jsonl"
        replay.write_text(json.dumps({"toy": actions}) + "\n")
        original = worker.build_config
        def replay_config(request):
            config, guidance = original(request)
            config["agent"]["model"] = {"name": "replay", "replay_path": str(replay)}
            return config, guidance
        monkeypatch.setattr(worker, "build_config", replay_config)
        monkeypatch.setenv("PHOENIX_API_KEY", "synthetic-unused-key")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        stage = tmp_path / "stage"
        worker.execute(dict(task=task, settings=settings, stage_dir=str(stage),
            base_agent=worker.prepare_tools(tmp_path)["agent"], port=port,
            run_id=run_id, job_id="one", candidate=candidate,
            generation_allowance=160, final_stage=True))
        patch = (stage / "P0.patch").read_text()
        assert "+value = 2" in patch and "-value = 1" in patch
        p0 = json.loads((stage / "P0.json").read_text())
        assert p0["mechanical_validation"]["valid"]
        assert p0["review_calls"] == p0["repair_calls"] == 0
        budget = json.loads((stage / "author_budget.json").read_text())
        assert budget["attempted_calls"] == 3
        provenance = json.loads((stage / "history_isolation.json").read_text())
        assert provenance["snapshot_commit"] != commit
    finally:
        owned = docker("ps", "-aq", "--filter", "label=phoenixrepair.run=" + run_id, check=False).stdout.split()
        if owned:
            docker("rm", "-f", *owned, check=False)
        docker("image", "rm", image, check=False)
