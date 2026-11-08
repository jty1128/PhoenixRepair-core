"""One fresh Docker checkout and one author trajectory. No reviewer is installed."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import sys

import yaml

from phoenix.p0_guidance import IMPLEMENTATION_GUIDANCE
from phoenix.prompts import PromptBuilder
from phoenix.settings import ROOT, model_config, write_json

RUNTIME_PYTHON = "/opt/phoenix-python/bin/python3"


def prepare_tools(output):
    data = yaml.safe_load((ROOT / "config/default.yaml").read_text())
    for bundle in data["agent"]["tools"]["bundles"]:
        src = ROOT / bundle["path"]
        dest = output / "runtime-tools" / src.name
        if not dest.exists():
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for file in (dest / "bin").iterdir():
                if not file.is_file():
                    continue
                text = file.read_text()
                first, sep, rest = text.partition("\n")
                if first.startswith("#!") and "python" in first:
                    file.write_text("#!" + RUNTIME_PYTHON + sep + rest)
            if src.name == "edit_anthropic":
                (dest / "install.sh").write_text(RUNTIME_PYTHON + " -c 'import tree_sitter, tree_sitter_languages'\n")
        bundle["path"] = str(dest.resolve())
    return data


def build_config(request):
    from sweagent.agent.agents import DefaultAgentConfig
    task, settings = request["task"], request["settings"]
    stage = Path(request["stage_dir"])
    data = copy.deepcopy(request["base_agent"])
    data.update(type="default", max_steps=settings["max_steps"], action_repeat_limit=0,
                limit_submission_mode="official_v2_staged_diff", localization_workflow=False,
                localization_independent_hypotheses=False, localization_complete_strategies=False)
    p0 = settings["method"] == "our_method"
    data["model"] = model_config(settings["model"], settings["api_base"], p0=p0)
    data["tools"].update(git_execution_timeout=180, max_consecutive_execution_timeouts=0)
    if p0:
        data["templates"]["instance_template"] += "\n" + IMPLEMENTATION_GUIDANCE
        data["tools"]["total_execution_timeout"] = settings["task_timeout"]
    agent = DefaultAgentConfig.model_validate(data)
    helper = PromptBuilder()
    if request.get("candidate") is not None:
        guidance = helper._generate_location_insights_from_patch(request["candidate"])
        agent = helper._enhance_agent_config_with_location_insights(agent, guidance)
    elif request.get("summary") is not None:
        guidance = request["summary"]
        original = agent.templates.instance_template
        agent = helper._enhance_agent_config_with_insights(agent, guidance, request["num_candidates"])
        # Treat model text literally; source examples may contain Jinja delimiters.
        prefix = agent.templates.instance_template.removesuffix(original + "\n")
        agent.templates.instance_template = "{{ " + json.dumps(prefix, ensure_ascii=False) + " }}" + original + "\n"
    else:
        guidance = ""
    runtime = Path(settings["runtime"])
    port = request["port"]
    docker_args = ["--network=host", "--user=0", "--entrypoint=", "--cpus=" + str(settings["container_cpus"]),
                   "--memory=" + settings["container_memory"], "--pids-limit=512",
                   "--label", "phoenixrepair.run=" + request["run_id"],
                   "--label", "phoenixrepair.job=" + request["job_id"],
                   "--mount", f"type=bind,src={runtime / 'python'},dst=/opt/phoenix-python,readonly",
                   "--mount", f"type=bind,src={runtime / 'swerex-remote'},dst=/usr/local/bin/swerex-remote,readonly",
                   "-e", f"PHOENIX_REX_PORT={port}", "-e", "PIP_DISABLE_PIP_VERSION_CHECK=1",
                   "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory",
                   "-e", "GIT_CONFIG_VALUE_0=/" + task["repo_name"]]
    config = {
        "agent": agent.model_dump(mode="json"),
        "env": {"repository_setup_timeout": 900,
                "post_startup_commands": ["export GOMAXPROCS=2; export GOFLAGS=\"${GOFLAGS:-} -p=2\""],
                "deployment": {"type": "docker", "image": task["image_name"], "pull": "never",
                               "python_standalone_dir": "", "port": port, "startup_timeout": 300,
                               "docker_args": docker_args},
                "repo": {"type": "preexisting", "repo_name": task["repo_name"], "base_commit": task["base_commit"]}},
        "problem_statement": {"type": "text", "id": "author", "text": task["problem_statement"]},
        "output_dir": str(stage / "generate"),
        "actions": {"open_pr": False, "apply_patch_locally": False},
    }
    config["agent"]["model"]["api_key"] = "$PHOENIX_API_KEY"
    return config, guidance


def install_p0_budget(agent, allowance, final_stage, stage=None):
    """Retain the historical author allowance and reminders, then stop at P0."""
    from sweagent.agent.hooks.abstract import AbstractAgentHook
    from sweagent.exceptions import InstanceCallLimitExceededError

    class AuthorBudget(AbstractAgentHook):
        def __init__(self):
            self.calls = 0

        def on_init(self, *, agent):
            original = agent.model.query

            def query(*args, **kwargs):
                if self.calls >= allowance:
                    raise InstanceCallLimitExceededError("Initial P0 generation allowance reached; collect the current patch")
                self.calls += 1
                return original(*args, **kwargs)
            agent.model.query = query
            if stage is not None:
                original_submit = agent.handle_submission

                def submit(step, **kwargs):
                    result = original_submit(step, **kwargs)
                    if result.done and result.submission:
                        patch = result.submission
                        mechanical = {}
                        try:
                            source = Path(__file__).with_name("submission_probe.py")
                            script = "/root/phoenix_submission_probe.py"
                            env = agent._env
                            env.write_file(script, source.read_text())
                            env.write_file("/root/phoenix_p0.patch", patch)

                            def operation(name):
                                request = dict(operation=name, root="/" + env.repo.repo_name, patch_path="/root/phoenix_p0.patch")
                                output = env.communicate(RUNTIME_PYTHON + " " + script + " " + shlex.quote(json.dumps(request)), check="ignore", timeout=120)
                                return json.loads(output.strip().splitlines()[-1])
                            mechanical = operation("inspect_patch")
                            test_only = (mechanical.get("forbidden_test_paths") and not mechanical.get("untracked")
                                         and mechanical.get("reverse_apply_exit_code") == 0
                                         and mechanical.get("patch_sha256") == mechanical.get("checkout_patch_sha256"))
                            if test_only:
                                (stage / "original-submission.patch").write_text(patch)
                                cleaned = operation("normalize_test_submission")
                                if not cleaned.get("production_diff_unchanged"):
                                    raise ValueError("Submission cleanup changed production code")
                                patch = cleaned.pop("patch")
                                mechanical = cleaned["mechanical_validation"]
                                write_json(stage / "submission-cleanup.json", cleaned)
                                result.submission = patch
                        except Exception as exc:
                            mechanical = {"valid": False, "validation_error": type(exc).__name__ + ": " + str(exc)[:800]}
                        (stage / "P0.patch").write_text(patch)
                        write_json(stage / "P0.json", {"model_calls": self.calls, "mechanical_validation": mechanical,
                            "review_calls": 0, "repair_calls": 0, "generation_model_stats": agent.model.stats.model_dump()})
                    return result
                agent.handle_submission = submit

        def on_model_query(self, *, messages, agent):
            remaining = allowance - self.calls
            if self.calls == 0 or remaining in (20, 10, 5, 2, 1):
                messages.append({"role": "user", "agent": agent, "content":
                    f"This stage allows {allowance} initial generation calls; {remaining} remain. "
                    + ("Review and one focused repair have separate reserved calls within the task total. " if final_stage else
                       "This is an intermediate candidate; independent review is reserved for the final patch after summary. ")
                    + "Complete the issue, run focused checks, remove temporary files and submit before this allowance ends."})
    hook = AuthorBudget()
    agent.add_hook(hook)
    return hook


def execute(request):
    from sweagent.environment import history_isolation
    from sweagent.run.hooks.abstract import RunHook
    from sweagent.run.run_single import RunSingle, RunSingleConfig
    from swerex.deployment.docker import DockerDeployment
    from swerex.utils.wait import _wait_until_alive

    stage = Path(request["stage_dir"])
    stage.mkdir(parents=True, exist_ok=True)
    config, guidance = build_config(request)
    (stage / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    (stage / "guidance.txt").write_text(guidance)
    write_json(stage / "instance.json", request["task"])

    async def bounded_wait(deployment, timeout=10):
        await _wait_until_alive(deployment.is_alive, timeout=timeout, function_timeout=deployment._runtime_timeout)
    DockerDeployment._wait_until_alive = bounded_wait

    class IsolateHistory(RunHook):
        def on_instance_start(self, *, index, env, problem_statement):
            script = "/root/phoenix_isolate_history.py"
            env.write_file(script, Path(history_isolation.__file__).read_text())
            command = " ".join(map(shlex.quote, [RUNTIME_PYTHON, script, "/" + env.repo.repo_name, env.repo.base_commit]))
            audit = json.loads(env.communicate(command, check="raise", timeout=480).strip().splitlines()[-1])
            env.repo.base_commit = audit["snapshot_commit"]
            write_json(stage / "history_isolation.json", audit)

    runner = RunSingle.from_config(RunSingleConfig.model_validate(config))
    runner.add_hook(IsolateHistory())
    hook = None
    if request["settings"]["method"] == "our_method":
        hook = install_p0_budget(runner.agent, request["generation_allowance"], request["final_stage"], stage)
    try:
        runner.run()
    finally:
        if hook:
            write_json(stage / "author_budget.json", {"attempted_calls": hook.calls,
                "generation_allowance": request["generation_allowance"], "review_calls": 0,
                "repair_calls": 0, "output_stage": "P0" if request["final_stage"] else "candidate"})
        runner.env.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text())
    os.environ["PHOENIX_TASK_ID"] = request["task"]["instance_id"]
    os.environ["PHOENIX_STAGE"] = request["job_id"]
    execute(request)


if __name__ == "__main__":
    main()
