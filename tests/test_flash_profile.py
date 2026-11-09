"""Offline checks that Flash uses GLM's complete P0 pipeline and parameters."""
import json
import os
import subprocess
import sys

from phoenix.recovery_profiles import recovery_profile
from phoenix.settings import ROOT, load_settings, localization_config, model_config, write_json
from phoenix.worker import build_config, prepare_tools


def test_flash_matches_glm_rendered_author_and_localization(tmp_path, task, candidate):
    glm = load_settings("our_method", ROOT / "configs/experiments/glm-5.2-p0-200calls-32k.yaml", {})
    flash = load_settings("our_method", ROOT / "configs/models/deepseek-v4.1-flash/our_method.yaml", {})
    assert {k: v for k, v in flash.items() if k not in ("model", "output")} == {
        k: v for k, v in glm.items() if k not in ("model", "output")}
    assert localization_config(flash) == localization_config(glm)
    assert {k: v for k, v in recovery_profile(flash).items() if k != "model"} == {
        k: v for k, v in recovery_profile(glm).items() if k != "model"}
    configs = []
    for settings in [glm, flash]:
        settings.update(runtime=str(tmp_path / "runtime"), output=str(tmp_path / "output"))
        request = dict(task=task, settings=settings, stage_dir=str(tmp_path / "stage"),
                       base_agent=prepare_tools(tmp_path)["agent"], port=54321,
                       run_id="fixture", job_id="one", candidate=candidate)
        config, _ = build_config(request)
        model = config["agent"]["model"]
        assert model.pop("name") == "openai/" + settings["model"]
        configs.append(config)
    assert configs[0] == configs[1]


def test_flash_shell_entry_runs_full_pipeline_dry_run(tmp_path, task):
    manifest = tmp_path / "instances.json"
    write_json(manifest, [task])
    result = subprocess.run(["bash", str(ROOT / "run_v41_flash_our_method.sh"),
        "--instances", str(manifest), "--runtime", str(tmp_path / "runtime"),
        "--output", str(tmp_path / "output"), "--dry-run"], cwd=tmp_path,
        env=dict(os.environ, PYTHON_BIN=sys.executable, LITELLM_LOCAL_MODEL_COST_MAP="True"),
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    settings = plan["settings"]
    assert settings["model"] == "deepseek-v4.1-flash"
    assert settings["stage"] == "all" and settings["locations"] is None
    assert (settings["max_steps"], settings["localization_max_steps"], settings["concurrency"]) == (200, 150, 4)
    assert plan["method_output"] == "P0"
    assert not plan["review_calls_enabled"] and not plan["repair_stage_enabled"]
    assert plan["model_profile"]["max_tokens"] == 32768
    assert model_config(settings["model"], settings["api_base"], p0=True)["retry"]["retries"] == 8
