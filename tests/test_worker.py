from types import SimpleNamespace

from jinja2 import Template
import pytest

from phoenix.worker import build_config, install_p0_budget, prepare_tools
from phoenix.settings import PROFILES, method_profile, load_settings


@pytest.mark.parametrize("model", list(PROFILES))
@pytest.mark.parametrize("mode", ["single", "tts", "our_method"])
def test_config_profiles_no_secret_and_literal_guidance(tmp_path, task, candidate, settings, model, mode):
    if mode == "our_method" and model == "kimi-k2.7-code":
        pytest.skip("P0 only has GLM/Qwen profiles")
    from sweagent.run.run_single import RunSingleConfig
    settings.update(load_settings(mode, overrides={"model": model, "output": settings["output"], "runtime": settings["runtime"]}))
    request = dict(task=task, settings=settings, stage_dir=str(tmp_path / "stage"),
        base_agent=prepare_tools(tmp_path)["agent"], port=54321, run_id="fixture", job_id="one",
        num_candidates=3, summary="Literal source: {{ unbound }} and {% invalid %}")
    data, guidance = build_config(request)
    config = RunSingleConfig.model_validate(data)
    completion = config.agent.model.completion_kwargs
    assert completion["max_tokens"] == method_profile(model, mode)["max_tokens"]
    assert completion["timeout"] == PROFILES[model]["timeout"]
    assert completion["tool_choice"] == PROFILES[model]["tool_choice"]
    assert config.agent.model.api_key.get_secret_value() == "$PHOENIX_API_KEY"
    assert config.agent.action_repeat_limit == 0
    prompt = Template(config.agent.templates.instance_template).render(problem_statement="Original issue")
    assert "{{ unbound }} and {% invalid %}" in prompt
    request.pop("summary")
    request["candidate"] = candidate
    candidate["hypothesis"] += " {{ missing }}"
    data, guidance = build_config(request)
    prompt = Template(data["agent"]["templates"]["system_template"] + "\n" +
                      data["agent"]["templates"]["instance_template"]).render(problem_statement="Original issue")
    assert "{{ missing }}" in prompt and "Original issue" in prompt


def test_p0_budget_charges_errors_and_stops_without_reviewer():
    from sweagent.exceptions import InstanceCallLimitExceededError
    class Agent:
        def __init__(self):
            def failing_query(*a, **kw):
                raise ConnectionError("synthetic failure")
            self.model = SimpleNamespace(query=failing_query)

        def add_hook(self, hook):
            hook.on_init(agent=self)

    agent = Agent()
    budget = install_p0_budget(agent, 2, True)
    messages = []
    budget.on_model_query(messages=messages, agent="main")
    assert "2 initial generation calls" in messages[-1]["content"]
    for _ in range(2):
        with pytest.raises(ConnectionError):
            agent.model.query([])
    with pytest.raises(InstanceCallLimitExceededError):
        agent.model.query([])
    assert budget.calls == 2


def test_context_override_reaches_worker_without_changing_generation(tmp_path, task, settings):
    from phoenix.settings import CONTEXT_DEFAULTS
    settings.update(method="single", model="glm-5.2")
    request = dict(task=task, settings=settings, stage_dir=str(tmp_path / "stage"),
        base_agent=prepare_tools(tmp_path)["agent"], port=54321, run_id="fixture", job_id="one")
    original, _ = build_config(request)
    settings.update(max_input_tokens=1048576, context_compaction_threshold=1030000,
                    context_compaction_target=1000000)
    expanded, _ = build_config(request)
    for field, default in CONTEXT_DEFAULTS.items():
        assert original["agent"]["model"][field] == default
        assert expanded["agent"]["model"][field] == settings[field]
        expanded["agent"]["model"][field] = default
    assert expanded == original


@pytest.mark.parametrize("overrides", [
    {"max_input_tokens": 120000},
    {"context_compaction_target": 1030001},
    {"max_input_tokens": True},
])
def test_invalid_context_limits_rejected_before_launch(tmp_path, overrides):
    from phoenix.settings import load_settings
    config = tmp_path / "config.yaml"
    config.write_text("model: glm-5.2\n")
    with pytest.raises(ValueError):
        load_settings("single", config, overrides)
