"""Offline transport faults must not turn unfinished generation into a submission."""
import copy
import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import litellm
import pytest
import requests

from phoenix.worker import install_p0_budget
from phoenix.settings import API_BASE, model_config
from sweagent.agent.agents import DefaultAgent, TemplateConfig
from sweagent.agent.api_errors import interrupted_patch_command
from sweagent.agent.models import GenericAPIModelConfig, LiteLLMModel
from sweagent.exceptions import InstanceCallLimitExceededError, ModelAPIError
from sweagent.tools.tools import ToolConfig, ToolHandler


def model_with_outcomes(outcomes):
    retry = {**model_config("glm-5.2", API_BASE, p0=True)["retry"], "min_wait": 0, "max_wait": 0}
    model = LiteLLMModel(GenericAPIModelConfig(name="openai/glm-5.2", api_key="offline",
        max_input_tokens=131072, retry=retry),
        SimpleNamespace(use_function_calling=False))
    requests_seen = []
    def send(messages, **kwargs):
        requests_seen.append(copy.deepcopy(messages))
        value = outcomes[len(requests_seen) - 1]
        if isinstance(value, BaseException):
            raise value
        return [value]
    model._single_query = send
    return model, requests_seen


def charge(model, tmp_path, allowance=160, initial_calls=0):
    agent = SimpleNamespace(model=model, handle_submission=lambda s, **kw: s)
    agent.add_hook = lambda hook: hook.on_init(agent=agent)
    return install_p0_budget(agent, allowance, True, tmp_path, initial_calls)


def transport_error(kind=litellm.Timeout):
    return kind("synthetic offline error", model="glm-5.2", llm_provider="openai")


@pytest.mark.parametrize("kind", [litellm.Timeout, litellm.APIConnectionError,
                                  litellm.InternalServerError, litellm.RateLimitError])
def test_same_request_is_retried_and_every_attempt_charged(tmp_path, kind):
    model, sent = model_with_outcomes([transport_error(kind), {"message": "continued"}])
    budget = charge(model, tmp_path)
    history = [{"role": "user", "content": "continue from the existing edits"}]
    assert model.query(history) == {"message": "continued"}
    assert len(sent) == budget.calls == 2 and sent[0] == sent[1]
    assert json.loads((tmp_path / "author_budget.json").read_text())["attempted_calls"] == 2


@pytest.mark.parametrize("error", [transport_error(litellm.AuthenticationError),
    transport_error(litellm.BadRequestError), requests.exceptions.SSLError("certificate verify failed")])
def test_permanent_failure_is_not_retried_or_masked_as_success(tmp_path, error):
    model, sent = model_with_outcomes([error])
    budget = charge(model, tmp_path)
    with pytest.raises(ModelAPIError) as caught:
        model.query([])
    assert len(sent) == budget.calls == 1
    assert caught.value.details["retryable"] is False


def test_transport_attempts_are_bounded(tmp_path):
    model, sent = model_with_outcomes([transport_error()] * 8)
    budget = charge(model, tmp_path)
    with pytest.raises(ModelAPIError) as caught:
        model.query([])
    assert len(sent) == budget.calls == caught.value.details["attempts"] == 8
    assert caught.value.details["retryable"]


def test_eighth_attempt_can_succeed_without_resetting_budget(tmp_path):
    model, sent = model_with_outcomes([transport_error()] * 7 + [{"message": "continued"}])
    budget = charge(model, tmp_path)
    assert model.query([]) == {"message": "continued"}
    assert len(sent) == budget.calls == 8
    assert all(message == sent[0] for message in sent)


def test_last_failed_call_is_interruption_not_normal_budget_submission(tmp_path):
    model, sent = model_with_outcomes([transport_error()])
    budget = charge(model, tmp_path, initial_calls=159)
    with pytest.raises(ModelAPIError) as caught:
        model.query([])
    assert budget.calls == 160 and len(sent) == 1
    assert caught.value.details["budget_exhausted"] and caught.value.details["attempts"] == 1


def test_already_exhausted_budget_sends_no_request(tmp_path):
    model, sent = model_with_outcomes([])
    charge(model, tmp_path, initial_calls=160)
    with pytest.raises(InstanceCallLimitExceededError):
        model.query([])
    assert not sent


def make_agent(monkeypatch, model, tmp_path):
    agent = DefaultAgent(templates=TemplateConfig(system_template="system", instance_template="issue"),
        tools=ToolHandler(ToolConfig()), history_processors=[], model=model, max_steps=200)
    agent._env = Mock()
    agent._env.repo = SimpleNamespace(repo_name="app")
    agent._env.communicate.return_value = "unfinished patch\n"
    agent.traj_path = tmp_path / "author.traj"
    agent.history = [{"role": "user", "content": "original issue", "agent": "main"}]
    monkeypatch.setattr(agent.tools, "get_state", lambda **kw: {"working_dir": "/app"})
    monkeypatch.setattr(agent, "_get_format_dict", lambda **kw: kw)
    monkeypatch.setattr(agent, "_get_edited_files_with_context", lambda **kw: {})
    agent.attempt_autosubmission_after_error = Mock(side_effect=AssertionError("must not autosubmit"))
    agent.handle_submission = Mock(wraps=agent.handle_submission)
    return agent


def test_exhausted_transport_preserves_patch_and_history_without_review(monkeypatch, tmp_path):
    model, _ = model_with_outcomes([transport_error()] * 8)
    agent = make_agent(monkeypatch, model, tmp_path)
    original_history = copy.deepcopy(agent.history)
    step = agent.step()
    assert step.done and step.exit_status == "exit_api" and step.submission is None
    assert agent.history == original_history
    assert (tmp_path / "api-interrupted.patch").read_text() == "unfinished patch\n"
    assert agent.info["api_error"]["retryable"]
    agent.attempt_autosubmission_after_error.assert_not_called()
    agent.handle_submission.assert_not_called()


def test_transient_error_continues_actions_in_same_agent(monkeypatch, tmp_path):
    answer = {"message": "continue", "tool_calls": [{"id": "call_1", "type": "function",
        "function": {"name": "bash", "arguments": '{"command":"echo resumed"}'}}]}
    model, sent = model_with_outcomes([transport_error(), answer])
    agent = make_agent(monkeypatch, model, tmp_path)
    step = agent.step()
    assert not step.done and step.action == "echo resumed"
    assert len(sent) == 2 and sent[0] == sent[1]
    agent.handle_submission.assert_called_once()
    agent._env.read_file.assert_not_called()
    assert not (tmp_path / "api-interrupted.patch").exists()


def test_interrupted_capture_keeps_staged_and_untracked_work_without_changing_index(tmp_path):
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo)
    git("init", "-q")
    (repo / "tracked.txt").write_text("base\n")
    git("add", "tracked.txt")
    git("-c", "user.name=Offline", "-c", "user.email=offline@example.test", "commit", "-qm", "base")
    (repo / "tracked.txt").write_text("staged\n")
    git("add", "tracked.txt")
    (repo / "tracked.txt").write_text("unfinished\n")
    (repo / "new.txt").write_text("new work\n")
    index_before = (repo / ".git/index").read_bytes()
    patch = subprocess.check_output(["bash", "-c", interrupted_patch_command(str(repo))], text=True)
    assert "+unfinished" in patch and "+new work" in patch
    assert (repo / ".git/index").read_bytes() == index_before
    assert (repo / "tracked.txt").read_text() == "unfinished\n"
