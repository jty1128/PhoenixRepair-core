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
import time
import signal
import threading

import yaml

from phoenix.p0_guidance import IMPLEMENTATION_GUIDANCE
from phoenix.prompts import PromptBuilder
from phoenix.settings import ROOT, model_config, write_json
from phoenix.run_state import read, digest, process_identity, lock
from phoenix.resources import io_slot

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
    data["model"] = model_config(settings["model"], settings["api_base"], p0=p0, context_settings=settings)
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
    docker_args = ["--network=host", "--user=0", "--entrypoint=", "--sig-proxy=false",
                   "--cpus=" + str(settings["container_cpus"]),
                   "--memory=" + settings["container_memory"], "--pids-limit=512",
                   "--label", "phoenixrepair.run=" + request["run_id"],
                   "--label", "phoenixrepair.job=" + request["job_id"],
                   "--mount", f"type=bind,src={runtime / 'python'},dst=/opt/phoenix-python,readonly",
                   "--mount", f"type=bind,src={runtime / 'swerex-remote'},dst=/usr/local/bin/swerex-remote,readonly",
                   "-e", f"PHOENIX_REX_PORT={port}", "-e", "PIP_DISABLE_PIP_VERSION_CHECK=1",
                   "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory",
                   "-e", "GIT_CONFIG_VALUE_0=/" + task["repo_name"]]
    if settings.get("pin_cpus"):
        cpus = sorted(os.sched_getaffinity(0))
        start = (port % max(1, len(cpus) // 2)) * 2
        docker_args.append("--cpuset-cpus=" + ",".join(map(str, cpus[start:start + 2])))
    if os.environ.get("PHOENIX_CONTAINER_PROXY"):
        # Inherit values through the worker environment, never serialize them.
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ[name] = os.environ["PHOENIX_CONTAINER_PROXY"]
            docker_args += ["-e", name]
        for name in ("NO_PROXY", "no_proxy"):
            os.environ[name] = "localhost,127.0.0.1,::1"
            docker_args += ["-e", name]
    config = {
        "agent": agent.model_dump(mode="json"),
        "env": {"repository_setup_timeout": 900,
                "post_startup_commands": ["export GOMAXPROCS=2; export GOFLAGS=\"${GOFLAGS:-} -p=2\""],
                "deployment": {"type": "docker", "image": request.get("image_id", task["image_name"]), "pull": "never",
                               "python_standalone_dir": "", "port": port, "startup_timeout": 300,
                               "docker_args": docker_args},
                "repo": {"type": "preexisting", "repo_name": task["repo_name"], "base_commit": task["base_commit"]}},
        "problem_statement": {"type": "text", "id": "author", "text": task["problem_statement"]},
        "output_dir": str(stage / "generate"),
        "actions": {"open_pr": False, "apply_patch_locally": False},
    }
    config["agent"]["model"]["api_key"] = "$PHOENIX_API_KEY"
    return config, guidance


def install_p0_budget(agent, allowance, final_stage, stage=None, initial_calls=0):
    """Retain the historical author allowance and reminders, then stop at P0."""
    from sweagent.agent.hooks.abstract import AbstractAgentHook
    from sweagent.exceptions import InstanceCallLimitExceededError

    class AuthorBudget(AbstractAgentHook):
        def __init__(self):
            self.calls = initial_calls

        def persist(self):
            if stage is not None:
                write_json(stage / "author_budget.json", {"attempted_calls": self.calls,
                    "generation_allowance": allowance, "review_calls": 0, "repair_calls": 0,
                    "output_stage": "P0" if final_stage else "candidate"})

        def on_init(self, *, agent):
            # Retries happen inside query(). Charge each individual attempt,
            # including failures, rather than only the outer query invocation.
            method = "_single_query" if hasattr(agent.model, "_single_query") else "query"
            original = getattr(agent.model, method)

            def query(*args, **kwargs):
                if self.calls >= allowance:
                    raise InstanceCallLimitExceededError("Initial P0 generation allowance reached; collect the current patch")
                self.calls += 1
                self.persist()  # Charge before the request, including failed/in-flight calls.
                return original(*args, **kwargs)
            setattr(agent.model, method, query)
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
                    + ("This is the final P0 stage; all stated calls are available for generation and checks. " if final_stage else
                       "This is an intermediate candidate; summary and final generation follow within the shared 200-call budget. ")
                    + "Complete the issue, run focused checks, remove temporary files and submit before this allowance ends."})
    hook = AuthorBudget()
    agent.add_hook(hook)
    return hook


def replay_patch(env, request, stage):
    recovery = request["recovery"]
    patch = Path(recovery["patch"])
    if digest(patch) != recovery["patch_sha256"]:
        raise ValueError("Preserved patch changed before replay")
    if recovery["mode"] == "continue" and not patch.read_bytes().strip():
        # A verified empty capture means the recovered base needs no edits.
        (stage / "replayed.patch").write_text("")
        result = {"restored_clean_base": True, "model_calls_used": 0}
        write_json(stage / "replay.json", result)
        return result
    env.write_file("/root/phoenix_submission_probe.py", Path(__file__).with_name("submission_probe.py").read_text())
    env.write_file("/root/phoenix_preserved.patch", patch.read_text())
    args = dict(operation="replay_submission", root="/" + env.repo.repo_name,
                patch_path="/root/phoenix_preserved.patch",
                clean_tests=request["settings"]["method"] == "our_method" and recovery["mode"] == "submission",
                expected_tree=recovery.get("patched_tree"))
    output = env.communicate(RUNTIME_PYTHON + " /root/phoenix_submission_probe.py " +
                             shlex.quote(json.dumps(args)), check="ignore", timeout=180)
    result = json.loads(output.strip().splitlines()[-1])
    (stage / "replayed.patch").write_text(result.pop("patch"))
    write_json(stage / "replay.json", {**result, "original_model_calls": request.get("prior_calls", 0),
                                     "budget_reset": False, "same_model": request["settings"]["model"]})
    return result


def install_durability(agent, request, budget):
    from sweagent.agent.hooks.abstract import AbstractAgentHook
    stage = Path(request["stage_dir"])

    class Durability(AbstractAgentHook):
        def __init__(self):
            self.calls = request.get("prior_calls", 0)

        def on_init(self, *, agent):
            self.agent = agent
            if budget is None:
                method = "_single_query" if hasattr(agent.model, "_single_query") else "query"
                original_attempt = getattr(agent.model, method)
                def counted_attempt(*a, **kw):
                    self.calls += 1
                    write_json(stage / "author_budget.json", {"attempted_calls": self.calls,
                        "generation_allowance": None, "review_calls": 0, "repair_calls": 0})
                    return original_attempt(*a, **kw)
                setattr(agent.model, method, counted_attempt)
            original = agent.model.query
            def charged(*a, **kw):
                if request.get("deadline") and time.time() >= request["deadline"]:
                    raise TimeoutError("Original generation deadline reached; no further model request")
                if budget is None or budget.calls < request["generation_allowance"]:
                    # An interrupted in-flight step is charged conservatively;
                    # it cannot disappear because no observation was written.
                    write_json(stage / "query-state.json", {
                        "reserved_step": request.get("steps_offset", 0) + len(agent.trajectory) + 1})
                    write_json(stage / "checkpoint.json", agent.get_trajectory_data())
                return original(*a, **kw)
            agent.model.query = charged

        def on_run_start(self):
            recovery = request.get("recovery") or {}
            path = recovery.get("trajectory")
            if path:
                if digest(path) != recovery["trajectory_sha256"]:
                    raise ValueError("Preserved trajectory changed before recovery")
                data = read(path)
                self.agent.history = data["history"]
                self.agent.trajectory[:] = data["trajectory"]
                # Tool execution allowance is cumulative too, including when
                # the previous container was replaced.
                self.agent._total_execution_time = request.get("prior_execution_seconds", 0)
                self.agent.info.update(data["info"])
                self.agent.info.update(submission=None, exit_status=None)
                self.agent.info.pop("api_error", None)
                stats = data["info"].get("model_stats")
                if stats:
                    self.agent.model.stats = type(self.agent.model.stats).model_validate(stats)
                self.agent.history.append({"role": "user", "agent": self.agent.name,
                    "content": "The execution environment was recovered after an infrastructure failure. "
                    "Your saved code and completed trajectory were restored. The last interrupted operation "
                    "may not have completed; inspect its current state before repeating it. "
                    "The original issue and remaining budget still apply."})
            elif request.get("prior_steps", 0):
                # Only used when the disposable container and recoverable diff
                # are both unavailable. Never grant another full trajectory.
                self.agent.history.append({"role": "user", "agent": self.agent.name,
                    "content": "The previous container was lost and no verified working patch was recoverable. "
                    "This is the original base checkout. Previously charged work remains charged; "
                    "continue under the remaining stage budget."})
            self.agent.max_steps = max(1, self.agent.max_steps - request.get("steps_offset", 0))
            self.agent._total_execution_time = max(self.agent._total_execution_time,
                                                   request.get("prior_execution_seconds", 0))
            write_json(stage / "worker-status.json", {"status": "generating", "at": time.time()})

        def on_step_done(self, *, step, info):
            # Atomic checkpoint replaces the upstream non-atomic .traj write as
            # the source for recovery. It contains no extra model-facing text.
            write_json(stage / "checkpoint.json", self.agent.get_trajectory_data())
            write_json(stage / "step-state.json", {
                "steps": request.get("steps_offset", 0) + len(self.agent.trajectory),
                "model_calls": budget.calls if budget else self.calls,
                "execution_seconds": self.agent._total_execution_time,
                "exit_status": info.get("exit_status"), "at": time.time()})
            if step.done and (step.submission is not None or str(step.exit_status).startswith('submitted')):
                (stage / "submitted.patch").write_text(step.submission or '')
                write_json(stage / "submission.json", {"patch_sha256": digest(stage / "submitted.patch"),
                    "model_calls": budget.calls if budget else self.calls, "exit_status": step.exit_status})

    hook = Durability()
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
    write_json(stage / "worker-status.json", {"status": "starting", "at": time.time()})
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
            if (request.get("recovery") or {}).get("mode") == "continue":
                replay_patch(env, request, stage)

    runner = RunSingle.from_config(RunSingleConfig.model_validate(config))
    if request.get("managed_recovery"):
        # RunSingle closes even after a handled environment error. The host
        # controller must inspect/capture that checkout before removing it.
        runner.env.close = lambda: None
        runner.env.deployment._controller_managed = True
    runner.add_hook(IsolateHistory())
    original_communicate = runner.env.communicate
    def bounded_commands(*a, **kw):
        with io_slot(Path(request["settings"]["output"]), "commands",
                     request["settings"].get("local_command_concurrency", 4),
                     request.get("deadline")):
            return original_communicate(*a, **kw)
    runner.env.communicate = bounded_commands
    original_start = runner.env.start
    def gated_start():
        with io_slot(Path(request["settings"]["output"]), "start",
                     request["settings"].get("environment_start_concurrency", 2),
                     request.get("deadline")):
            return original_start()
    runner.env.start = gated_start
    hook = None
    if request["settings"]["method"] == "our_method":
        hook = install_p0_budget(runner.agent, request["generation_allowance"], request["final_stage"],
                                 stage, request.get("prior_calls", 0))
        hook.persist()
    install_durability(runner.agent, request, hook)
    failed = False
    try:
        if (request.get("recovery") or {}).get("mode") == "submission":
            runner.env.start()
            IsolateHistory().on_instance_start(index=0, env=runner.env, problem_statement=runner.problem_statement)
            replay = replay_patch(runner.env, request, stage)
            patch = (stage / "replayed.patch").read_text()
            (stage / "submitted.patch").write_text(patch)
            if request["settings"]["method"] == "our_method":
                (stage / "P0.patch").write_text(patch)
                write_json(stage / "P0.json", {"model_calls": request.get("prior_calls", 0),
                    "mechanical_validation": replay["mechanical_validation"], "review_calls": 0, "repair_calls": 0})
            write_json(stage / "submission.json", {"patch_sha256": digest(stage / "submitted.patch"),
                "model_calls": request.get("prior_calls", 0), "exit_status": "submitted (recovered)"})
        else:
            runner.run()
        write_json(stage / "worker-status.json", {"status": "completed", "at": time.time()})
    except BaseException as exc:
        failed = True
        write_json(stage / "worker-status.json", {"status": "failed", "error_type": type(exc).__name__, "at": time.time()})
        raise
    finally:
        if hook:
            write_json(stage / "author_budget.json", {"attempted_calls": hook.calls,
                "generation_allowance": request["generation_allowance"], "review_calls": 0,
                "repair_calls": 0, "output_stage": "P0" if request["final_stage"] else "candidate"})
        # Keep a failed managed container for stopped-tree capture by its
        # controller; the controller always owns final cleanup.
        if not failed or not request.get("managed_recovery"):
            runner.env.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text())
    with lock(Path(request['stage_dir']) / 'worker.lock', blocking=False):
        run_owned(request)


def run_owned(request):
    ident = process_identity(os.getpid())
    if ident["pgid"] == os.getpid():
        process = {"pid": os.getpid(), "start_ticks": ident["start_ticks"],
                   "started_at": request.get("started_at", time.time())}
        write_json(Path(request["stage_dir"]) / "process.json", process)
    os.environ["PHOENIX_TASK_ID"] = request["task"]["instance_id"]
    os.environ["PHOENIX_STAGE"] = request["job_id"]
    done = threading.Event()
    def watchdog():
        if done.wait(max(0, request["deadline"] - time.time())):
            return
        stage = Path(request["stage_dir"])
        write_json(stage / "termination.json", {"reason": "wall_timeout", "at": time.time()})
        # The same deadline blocks every new query/command. Quiesce any
        # in-flight container command before capture, even while the controller
        # is offline. The controller's parallel capture is serialized by lock.
        from phoenix.capture import capture_checkout
        try:
            capture_checkout(request, os.environ.copy(), stage,
                official=request["settings"]["method"] != "our_method"
                         and request["settings"]["model"] != "kimi-k2.7-code",
                timeout=request.get("capture_reserve", 60))
        finally:
            current = process_identity(os.getpid())
            if current and current['start_ticks'] == ident['start_ticks'] and current['pgid'] == os.getpid():
                os.killpg(os.getpid(), signal.SIGKILL)
    if request.get("managed_recovery") and request.get("deadline"):
        if ident["pgid"] != os.getpid():
            raise RuntimeError("Managed worker must have its own process group")
        # Keep the interpreter alive until an already-started timeout capture
        # finishes. Normal completion sets done and wakes this thread at once.
        threading.Thread(target=watchdog, daemon=False, name="generation-deadline").start()
    try:
        execute(request)
    finally:
        done.set()


if __name__ == "__main__":
    main()
