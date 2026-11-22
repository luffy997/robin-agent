import pytest

from robin import (
    FakeModelClient,
    ModelResponse,
    Robin,
    SessionStore,
    ToolCall,
    Usage,
    WorkspaceContext,
)
from robin.providers.context import build_request
from robin.providers.types import text_response
from robin.cli import build_arg_parser
from robin.usage import empty_usage


def agent_at(path, responses, **kwargs):
    return Robin(
        FakeModelClient(responses),
        WorkspaceContext.build(path),
        SessionStore(path / ".robin/sessions"),
        approval_policy=kwargs.pop("approval_policy", "auto"),
        **kwargs,
    )


def test_multiple_calls_execute_in_order_with_budget_results(tmp_path):
    response = ModelResponse(
        tool_calls=[
            ToolCall("write_file", {"path": "a", "content": "first"}, "one"),
            ToolCall(
                "patch_file",
                {"path": "a", "old_text": "first", "new_text": "second"},
                "two",
            ),
            ToolCall("write_file", {"path": "b", "content": "blocked"}, "three"),
        ],
        stop_reason="tool_use",
        usage=Usage(100, 20, 50),
        reasoning="hidden",
        replay={"reasoning_content": "hidden"},
    )
    agent = agent_at(tmp_path, [response, text_response("done")], max_steps=2)
    assert agent.ask("create files") == "工具预算已耗尽：2/2，任务未完成\n\ndone"
    assert (tmp_path / "a").read_text() == "second"
    assert not (tmp_path / "b").exists()
    assert agent.current_task_state.tool_steps == 2
    messages = agent.model_client.requests[-1].messages
    results = [m for m in messages if m["role"] == "tool"]
    assert [r["tool_call_id"] for r in results] == ["one", "two", "three"]
    assert "budget" in results[-1]["content"].lower()
    assert agent.turn_usage["calls"] == 2
    assert agent.turn_usage["totals"]["input_tokens"] == 100
    stored = agent.session_store.load(agent.session["id"])
    assert stored["format_version"] == 2
    assistant = next(m for m in stored["history"] if m.get("tool_calls"))
    assert assistant["tool_calls"][0]["id"] == "one"
    assert assistant["replay"]["reasoning_content"] == "hidden"


@pytest.mark.parametrize(
    "name,args,policy,expected",
    [
        (
            "write_file",
            {"path": "blocked", "content": "no"},
            "never",
            "approval denied",
        ),
        ("read_file", {"path": "missing"}, "auto", "invalid arguments"),
        ("patch_file", {"path": "README.md"}, "auto", "invalid arguments"),
    ],
)
def test_rejection_returns_result_and_continues(tmp_path, name, args, policy, expected):
    agent = agent_at(
        tmp_path,
        [
            ModelResponse(
                tool_calls=[ToolCall(name, args, "one")], stop_reason="tool_use"
            ),
            text_response("handled"),
        ],
        approval_policy=policy,
    )
    assert agent.ask("do it") == "handled"
    result = next(
        m for m in agent.model_client.requests[-1].messages if m["role"] == "tool"
    )
    assert expected in result["content"]
    assert result["tool_call_id"] == "one"


def test_tool_failure_is_sent_to_model(tmp_path):
    agent = agent_at(
        tmp_path,
        [
            ModelResponse(
                tool_calls=[ToolCall("list_files", {}, "one")], stop_reason="tool_use"
            ),
            text_response("handled"),
        ],
    )

    def fail(*args, **kwargs):
        raise OSError("test failure")

    agent.tools["list_files"]["run"] = fail
    assert agent.ask("list") == "handled"
    assert "test failure" in agent.session["history"][2]["content"]


@pytest.mark.parametrize("reason", ["length", "error", "aborted", "unknown"])
def test_abnormal_stop_never_executes_tools(tmp_path, reason):
    response = ModelResponse(
        text="partial",
        tool_calls=[ToolCall("write_file", {"path": "a", "content": "bad"})],
        stop_reason=reason,
        usage=Usage(10, 2),
    )
    agent = agent_at(tmp_path, [response])
    with pytest.raises(RuntimeError, match="stopped") as error:
        agent.ask("go")
    if reason == "length":
        assert "16384 tokens" in str(error.value)
        assert "--max-new-tokens" in str(error.value)
        assert "--resume latest" in str(error.value)
    assert not (tmp_path / "a").exists()
    assert agent.current_task_state.status == "failed"
    assert agent.turn_usage["calls"] == 1


@pytest.mark.parametrize("limit", [None, 16384])
def test_output_budget_reaches_model_request(tmp_path, limit):
    argv = [] if limit is None else ["--max-new-tokens", str(limit)]
    args = build_arg_parser().parse_args(argv)
    kwargs = {} if limit is None else {"max_new_tokens": args.max_new_tokens}
    agent = agent_at(tmp_path, [text_response("done")], **kwargs)
    assert agent.ask("hello") == "done"
    expected = 16384 if limit is None else limit
    assert args.max_new_tokens == limit
    assert agent.model_client.requests[0].max_output_tokens == expected


def test_truncation_reports_configured_budget_without_repeating_completed_tools(tmp_path):
    agent = agent_at(
        tmp_path,
        [
            ModelResponse(tool_calls=[ToolCall("write_file", {"path": "done", "content": "ok"})],
                          stop_reason="tool_use"),
            ModelResponse(tool_calls=[ToolCall("write_file", {"path": "partial", "content": "bad"})],
                          stop_reason="length", usage=Usage(100, 512)),
        ],
        max_new_tokens=512,
    )
    with pytest.raises(RuntimeError, match="configured limit: 512 tokens"):
        agent.ask("create files")
    assert (tmp_path / "done").read_text() == "ok"
    assert not (tmp_path / "partial").exists()
    assert agent.current_task_state.tool_steps == 1
    assert len(agent.model_client.requests) == 2
    assert agent.turn_usage["calls"] == 2
    assert agent.turn_usage["totals"]["output_tokens"] == 512


def test_legacy_history_summary_preserves_usage(tmp_path):
    agent = agent_at(tmp_path, [])
    session = agent.session
    session.pop("format_version")
    session["history"] = [
        {"role": "user", "content": "old goal"},
        {"role": "tool", "name": "read_file", "content": "old fact"},
    ]
    session["usage"]["calls"] = 3
    resumed = agent_at(tmp_path, [text_response("ok")], session=session)
    assert resumed.ask("continue") == "ok"
    messages = resumed.model_client.requests[0].messages
    assert "Legacy session history" in messages[0]["content"]
    assert "old fact" in messages[0]["content"]
    assert all(m["role"] != "tool" for m in messages)
    assert resumed.session["usage"]["calls"] == 4
    resumed.reset()
    assert "legacy_summary" not in resumed.session
    assert resumed.session["usage"] == empty_usage()


def test_history_trimming_never_splits_call_group(tmp_path):
    agent = agent_at(tmp_path, [])
    agent.session["history"] = [
        {"role": "user", "content": "old"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "old",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "content": "x" * 1000, "tool_call_id": "old"},
        {"role": "user", "content": "new"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "new",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "content": "result", "tool_call_id": "new"},
    ]
    req = build_request(
        agent, "system\n\nTranscript:\nold", {"section_budgets": {"history": 300}}
    )
    ids = {c["id"] for m in req.messages for c in m.get("tool_calls", [])}
    result_ids = {m["tool_call_id"] for m in req.messages if m["role"] == "tool"}
    assert ids == result_ids == {"old", "new"}
    assert {"role": "user", "content": "new"} in req.messages
    # An interrupted group is omitted, never sent as a dangling native call.
    agent.session["history"].pop()
    req = build_request(agent, "system", {})
    assert all(c["id"] != "new" for m in req.messages for c in m.get("tool_calls", []))
