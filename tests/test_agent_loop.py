from robin.providers.types import ModelError, ModelResponse, text_response, tool_response
import json

import pytest

from robin import FakeModelClient, Robin, SessionStore, WorkspaceContext
from robin.agent_loop import AgentLoop


def build_agent(tmp_path, outputs, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".robin" / "sessions")
    return Robin(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
        **kwargs,
    )


def test_agent_loop_runs_same_control_flow_as_robin_ask(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            tool_response('read_file', {'path': 'hello.txt', 'start': 1, 'end': 1}),
            text_response('Done.'),
        ],
    )

    answer = AgentLoop(agent).run("Inspect hello.txt")

    assert answer == "Done."
    assert agent.current_task_state.status == "completed"
    assert agent.run_store.report_path(agent.current_task_state.run_id).exists()


def test_robin_ask_delegates_to_agent_loop(tmp_path):
    agent = build_agent(tmp_path, [text_response('Facade works.')])

    assert agent.ask("Use facade") == "Facade works."


@pytest.mark.parametrize("commands,executed", [(["A", "A", "A"], 2), (["A", "B", "A", "B"], 3)])
def test_auto_blocks_repeated_calls_before_approval(tmp_path, commands, executed):
    from unittest.mock import Mock

    agent = build_agent(tmp_path, [
        *(tool_response("run_shell", {"command": command}) for command in commands),
        text_response("stopped repeating"),
    ], max_steps=8)
    runner = Mock(return_value="exit_code: 0\nstdout: ok")
    agent.tools["run_shell"]["run"] = runner
    agent.approve = Mock(return_value=True)
    agent.ask("inspect")
    assert runner.call_count == executed
    assert agent.approve.call_count == executed
    assert "repeated tool call loop" in agent.session["history"][-2]["content"]
    assert agent.current_task_state.tool_steps == len(commands)


def test_repetition_does_not_leak_across_user_requests(tmp_path):
    agent = build_agent(tmp_path, [
        tool_response("list_files", {}), tool_response("list_files", {}), text_response("first"),
        tool_response("list_files", {}), text_response("second"),
    ])
    agent.ask("first")
    agent.ask("second")
    assert "repeated" not in agent.session["history"][-2]["content"]


@pytest.mark.parametrize("summary", [
    text_response("summary"), text_response(""),
    tool_response("write_file", {"path": "forbidden", "content": "bad"}),
    ModelError("offline"), ModelResponse(stop_reason="length"),
])
def test_budget_stop_survives_all_summary_outcomes(tmp_path, summary, capsys):
    agent = build_agent(tmp_path, [tool_response("list_files", {}), summary], max_steps=1)
    final = agent.ask("inspect")
    state = agent.current_task_state
    notice = "工具预算已耗尽：1/1，任务未完成"
    assert notice in capsys.readouterr().out
    assert final.startswith(notice)
    assert state.status == "stopped" and state.stop_reason == "budget_exhausted"
    assert len(agent.model_client.requests) == 2
    assert agent.model_client.requests[-1].tools == []
    assert not (tmp_path / "forbidden").exists()
    result = agent.execute_tool("write_file", {"path": "after", "content": "bad"})
    assert result.metadata["tool_error_code"] == "budget_exhausted"
    assert not (tmp_path / "after").exists()
    report = json.loads(agent.run_store.report_path(state.run_id).read_text())
    assert report["stop_reason"] == "budget_exhausted"
    stored = json.loads(agent.run_store.task_state_path(state.run_id).read_text())
    assert stored["stop_reason"] == "budget_exhausted"


def test_agent_loop_reserves_a_final_answer_after_tool_budget_is_exhausted(tmp_path):
    (tmp_path / "facts.txt").write_text("one\ntwo\nthree\nfour\nfive\nsix\n", encoding="utf-8")
    tool_outputs = [
        tool_response("read_file", {"path": "facts.txt", "start": line, "end": line})
        for line in range(1, 7)
    ]
    agent = build_agent(
        tmp_path,
        [*tool_outputs, text_response('All six facts were inspected.')],
        max_steps=6,
    )

    answer = agent.ask("Inspect all facts and summarize them")

    assert answer == "工具预算已耗尽：6/6，任务未完成\n\nAll six facts were inspected."
    assert agent.current_task_state.status == "stopped"
    assert agent.current_task_state.stop_reason == "budget_exhausted"
    assert agent.model_client.requests[-1].tools == []
    assert agent.current_task_state.tool_steps == 6
    assert agent.current_task_state.attempts == 7
    trace_path = agent.run_store.trace_path(agent.current_task_state)
    trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert any(
        event["event"] == "model_requested" and event.get("purpose") == "finalization"
        for event in trace_events
    )


def test_agent_loop_persists_model_failure_before_reraising(tmp_path):
    class FailingModelClient:
        supports_prompt_cache = False

        def complete(self, *args, **kwargs):
            raise ModelError(
                "Anthropic-compatible response ended before a text block "
                "(stop_reason=max_tokens, content_types=thinking)",
                ModelResponse(metadata={"stop_reason": "max_tokens", "content_block_types": ["thinking"]})
            )

    agent = build_agent(tmp_path, [])
    agent.model_client = FailingModelClient()

    with pytest.raises(RuntimeError, match="ended before a text block"):
        agent.ask("Inspect the tests")

    state = agent.current_task_state
    assert state.status == "failed"
    assert state.stop_reason == "model_error"
    assert state.attempts == 1
    assert agent.run_store.task_state_path(state).exists()
    assert agent.run_store.report_path(state).exists()
    report = agent.run_store.load_report(state.run_id)
    assert report["stop_reason"] == "model_error"
    assert report["prompt_metadata"]["stop_reason"] == "max_tokens"
