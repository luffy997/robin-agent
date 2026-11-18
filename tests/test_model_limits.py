import json

import pytest

from robin.cli import build_agent, build_arg_parser
from robin.providers.limits import model_limits, output_budget
from robin.providers.types import text_response
from test_native_loop import agent_at


def test_catalog_defaults_and_exact_provider_model_lookup(tmp_path):
    assert model_limits(tmp_path, "deepseek", "custom") == (16384, 128000)
    (tmp_path / "robin.models.json").write_text(json.dumps({
        "deepseek": {"custom": {"maxTokens": 32000, "contextWindow": 200000}}
    }))
    assert model_limits(tmp_path, "deepseek", "custom") == (32000, 200000)
    assert model_limits(tmp_path, "openai", "custom") == (16384, 128000)


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "invalid"])
def test_invalid_catalog_limits_fail(tmp_path, bad):
    (tmp_path / "robin.models.json").write_text(json.dumps({
        "deepseek": {"custom": {"maxTokens": bad}}
    }))
    with pytest.raises(ValueError, match="Invalid model limits"):
        model_limits(tmp_path, "deepseek", "custom")


def test_limit_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBIN_PROVIDER", "ollama")
    monkeypatch.delenv("ROBIN_API", raising=False)
    monkeypatch.delenv("ROBIN_MAX_NEW_TOKENS", raising=False)
    monkeypatch.delenv("ROBIN_CONTEXT_WINDOW", raising=False)
    (tmp_path / "robin.models.json").write_text(json.dumps({
        "ollama": {"custom": {"maxTokens": 20000, "contextWindow": 200000}}
    }))
    argv = ["--cwd", str(tmp_path), "--model", "custom"]
    def make(extra=()):
        return build_agent(build_arg_parser().parse_args([*argv, *extra]))
    agent = make()
    assert (agent.max_new_tokens, agent.context_window) == (20000, 200000)
    monkeypatch.setenv("ROBIN_MAX_NEW_TOKENS", "24000")
    monkeypatch.setenv("ROBIN_CONTEXT_WINDOW", "240000")
    agent = make()
    assert (agent.max_new_tokens, agent.context_window) == (24000, 240000)
    agent = make(["--max-new-tokens", "30000", "--context-window", "300000"])
    assert (agent.max_new_tokens, agent.context_window) == (30000, 300000)


def test_context_budget_includes_tools_and_non_ascii():
    messages = [{"role": "user", "content": "hello"}]
    tools = [{"description": "tool" * 100}]
    limit, estimate = output_budget(messages, tools, 16000, 10000)
    assert limit == 10000 - estimate - 4096
    assert output_budget(messages, [], 16000, 10000)[0] > limit
    assert output_budget([{"role": "user", "content": "你好世界啊"}], tools, 16000, 10000)[1] > estimate
    assert output_budget(messages, tools, 100, 10000)[0] == 100


def test_context_exhaustion_persists_failure_without_model_call(tmp_path):
    agent = agent_at(tmp_path, [], context_window=4096)
    with pytest.raises(RuntimeError, match="context budget exhausted"):
        agent.ask("hello")
    assert agent.model_client.requests == []
    assert agent.current_task_state.status == "failed"
    assert agent.turn_usage["calls"] == 0


def test_resume_detects_changed_context_window_and_accepts_legacy_identity(tmp_path):
    agent = agent_at(tmp_path, [text_response("done")], context_window=128000)
    agent.ask("hello")
    assert agent.session["runtime_identity"]["context_window"] == 128000
    resumed = agent_at(tmp_path, [], session=agent.session_store.load(agent.session["id"]),
                       context_window=8192)
    assert resumed.resume_state["status"] == "workspace-mismatch"
    assert "context_window" in resumed.resume_state["runtime_identity_mismatch_fields"]
    legacy = agent.session_store.load(agent.session["id"])
    legacy["runtime_identity"].pop("context_window", None)
    for checkpoint in legacy["checkpoints"]["items"].values():
        checkpoint["runtime_identity"].pop("context_window", None)
    resumed = agent_at(tmp_path, [], session=legacy, context_window=8192)
    assert resumed.resume_state["status"] == "full-valid"
