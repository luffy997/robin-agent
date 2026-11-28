import json

import pytest

from robin.providers.context import build_request
from robin.providers.types import ModelResponse, ToolCall, Usage, text_response
from test_native_loop import agent_at


def seed_history(agent):
    agent.session["history"] = [{"role": "user", "content": "add HTTP CLI"}]
    for i in range(12):
        agent.session["history"].extend([
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": str(i), "type": "function", "function": {
                    "name": "patch_file", "arguments": json.dumps({"path": "app.py", "content": "x" * 2500})}}]},
            {"role": "tool", "name": "patch_file", "args": {}, "tool_call_id": str(i),
             "content": "error: missing path" if i == 0 else "read " + "x" * 2500},
        ])


def test_large_history_preserved_while_capacity_available(tmp_path):
    agent = agent_at(tmp_path, [])
    seed_history(agent)
    request = build_request(agent, "system", {"section_budgets": {"history": 100}})
    assert len(request.messages) == 26
    assert "history_compaction" not in agent.session
    assert agent.model_client.requests == []


def test_model_tokenizer_used_for_compaction_summary_and_final_budget(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from robin.providers import clients
    from robin.providers.limits import estimate_input

    counted = []

    def count(**kwargs):
        counted.append(kwargs)
        return estimate_input(kwargs["messages"], kwargs["tools"] or [])

    monkeypatch.setattr(clients, "_library", lambda: SimpleNamespace(token_counter=count))
    agent = agent_at(tmp_path, [text_response("Goal: fix CLI; Failed: missing path")],
                     context_window=24000, max_new_tokens=2000)
    agent.model_client.model = "custom-model"
    seed_history(agent)
    metadata = {}
    build_request(agent, "system", metadata)
    assert metadata["history_compacted"]
    assert len(counted) >= 5
    assert all(call["model"] == "custom-model" for call in counted)
    assert any("Summarize this conversation" in call["messages"][0]["content"] for call in counted)
    assert counted[-1]["tools"]


def test_compaction_keeps_groups_goal_usage_and_persists_resume(tmp_path):
    summary = ModelResponse(text="Done: handler. Failed: missing path. Next: fix CLI, test.", usage=Usage(123, 45))
    agent = agent_at(tmp_path, [summary], context_window=24000, max_new_tokens=2000)
    seed_history(agent)
    original = json.loads(json.dumps(agent.session["history"]))
    agent.tool_executor._read_pages.append(((1, "app", "hash"), (1, 20)))
    metadata = {}
    request = build_request(agent, "system", metadata)
    assert metadata["history_compacted"]
    assert "missing path" in agent.model_client.requests[0].messages[1]["content"]
    assert agent.model_client.requests[0].tools == []
    assert agent.turn_usage["calls"] == 1
    assert agent.turn_usage["totals"]["input_tokens"] == 123
    assert agent.session["history"] == original
    assert not agent.tool_executor._read_pages
    assert {"role": "user", "content": "add HTTP CLI"} in request.messages
    calls = {c["id"] for m in request.messages for c in m.get("tool_calls", [])}
    assert calls == {m["tool_call_id"] for m in request.messages if m["role"] == "tool"}
    stored = agent.session_store.load(agent.session["id"])
    resumed = agent_at(tmp_path, [], session=stored, context_window=24000, max_new_tokens=2000)
    again = build_request(resumed, "system", {})
    assert any(summary.text in m["content"] for m in again.messages)
    assert resumed.model_client.requests == []
    resumed.reset()
    assert "history_compaction" not in resumed.session


@pytest.mark.parametrize("response", [RuntimeError("offline"), text_response(""),
                                    ModelResponse(text="partial", stop_reason="length"),
                                    ModelResponse(tool_calls=[ToolCall("run_shell", {"command": "bad"})])])
def test_compaction_failure_preserves_history_and_boundary(tmp_path, response):
    agent = agent_at(tmp_path, [response], context_window=24000, max_new_tokens=2000)
    seed_history(agent)
    original = json.loads(json.dumps(agent.session["history"]))
    with pytest.raises(RuntimeError, match="compaction failed"):
        build_request(agent, "system", {})
    assert agent.session["history"] == original
    assert "history_compaction" not in agent.session
    assert agent.turn_usage["calls"] == 1


def test_compaction_is_billed_once_in_agent_loop(tmp_path):
    agent = agent_at(tmp_path, [text_response("checkpoint: fix CLI"), text_response("done")],
                     context_window=24000, max_new_tokens=2000)
    seed_history(agent)
    assert agent.ask("continue") == "done"
    assert agent.turn_usage["calls"] == 2
    assert agent.current_task_state.tool_steps == 0


def test_repeated_compaction_updates_previous_checkpoint(tmp_path):
    agent = agent_at(tmp_path, [text_response("first checkpoint"), text_response("second checkpoint")],
                     context_window=24000, max_new_tokens=2000)
    seed_history(agent)
    build_request(agent, "system", {})
    first_boundary = agent.session["history_compaction"]["through"]
    for i in range(6):
        agent.session["history"].extend([
            {"role": "user", "content": "next " + "y" * 3000},
            {"role": "assistant", "content": "pending " + "z" * 3000},
        ])
    request = build_request(agent, "system", {})
    assert "first checkpoint" in agent.model_client.requests[1].messages[1]["content"]
    assert agent.session["history_compaction"]["through"] > first_boundary
    assert any("second checkpoint" in m["content"] for m in request.messages)


def test_latest_exchange_too_large_does_not_drop_or_call_model(tmp_path):
    agent = agent_at(tmp_path, [], context_window=12000, max_new_tokens=2000)
    agent.session["history"] = [{"role": "user", "content": "x" * 40000}]
    with pytest.raises(ValueError, match="latest exchange"):
        build_request(agent, "system", {})
    assert len(agent.session["history"][0]["content"]) == 40000
    assert agent.model_client.requests == []


def test_manual_compaction_forces_short_history_and_skips_empty(tmp_path):
    agent = agent_at(tmp_path, [text_response("Goal: hello; Done: greeted")])
    metadata = {}
    build_request(agent, "system", metadata, force_compaction=True)
    assert not metadata["history_compacted"]
    assert not agent.model_client.requests
    agent.session["history"] = [{"role": "user", "content": "hello"},
                                {"role": "assistant", "content": "hi"}]
    original = list(agent.session["history"])
    build_request(agent, "system", metadata, force_compaction=True)
    assert metadata["history_compacted"]
    assert len(agent.model_client.requests) == 1
    assert agent.session["history"] == original
    assert agent.session_store.load(agent.session["id"])["history_compaction"]["summary"]
    build_request(agent, "system", metadata, force_compaction=True)
    assert not metadata["history_compacted"]
    assert len(agent.model_client.requests) == 1
