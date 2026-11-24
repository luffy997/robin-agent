from robin.providers.types import Usage, ModelError, ModelResponse
from robin.providers.types import text_response, tool_response
import pytest

from robin import Robin, SessionStore, WorkspaceContext
from robin.providers.clients import normalize_response
from robin.terminal import render_toolbar
from robin.usage import add_usage, count_text, empty_usage, reasoning_status, status_lines


class Client:
    thinking = {"type": "disabled"}

    def __init__(self, outputs):
        self.outputs = iter(outputs)

    def complete(self, *args, **kwargs):
        result = next(self.outputs)
        if isinstance(result, Exception):
            raise ModelError(str(result), ModelResponse(usage=Usage(input_tokens=100, output_tokens=20, cached_tokens=50)))
        result.usage = Usage(input_tokens=100, output_tokens=20, cached_tokens=50)
        return result


def agent_at(path, outputs, **kwargs):
    return Robin(Client(outputs), WorkspaceContext.build(path), SessionStore(path / ".robin/sessions"), **kwargs)


def test_provider_usage_and_missing_cache():
    response = normalize_response({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 60, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 20}}})
    assert response.usage.input_tokens == 60
    assert response.usage.cached_tokens == 20
    assert normalize_response({}).usage.cached_tokens is None


def test_unknown_partial_and_toolbar_width():
    usage = empty_usage()
    add_usage(usage, {"input_tokens": 1200, "output_tokens": 300, "cached_tokens": 800})
    assert "66.7%" in status_lines(usage, usage, Client([]))[1]
    add_usage(usage, {})
    assert count_text(usage, "input_tokens") == "1,200(不完整)"
    assert count_text(usage, "cache_write_tokens") == "未知"
    from prompt_toolkit.utils import get_cwidth
    for width in (20, 60, 120):
        assert all(get_cwidth(line) <= width for line in render_toolbar(lambda: status_lines(usage, usage, Client([])), width).splitlines())


def test_context_status_prefers_reported_input_and_reset(tmp_path):
    agent = agent_at(tmp_path, [text_response("ok")])
    def lines():
        return status_lines(agent.turn_usage, agent.session["usage"], agent.model_client,
                            agent.context_usage, agent.context_window)
    assert "未知 / 128,000 token" in lines()[-1]
    agent.ask("hello")
    used = agent.context_usage["estimated_input_tokens"]
    assert used == agent.last_prompt_metadata["estimated_input_tokens"]
    assert "上下文（接口） 100 / 128,000 token" in lines()[-1]
    assert f"{100 * 100 / 128000:.1f}%" in lines()[-1]
    from prompt_toolkit.utils import get_cwidth
    for width in (20, 60, 120):
        assert all(get_cwidth(line) <= width for line in render_toolbar(lines, width).splitlines())
    agent.reset()
    assert "未知" in lines()[-1]


def test_context_zero_and_missing_reported_usage():
    usage = empty_usage()
    def line(context):
        return status_lines(usage, usage, Client([]), context, 1000)[-1]
    assert "（接口） 0 / 1,000 token (0.0%)" in line({"input_tokens": 0, "estimated_input_tokens": 300})
    assert "（估算） 约 300" in line({"input_tokens": None, "estimated_input_tokens": 300})
    assert "未知" in line({})


def test_loop_delegate_resume_and_reset(tmp_path):
    agent = agent_at(tmp_path, [
        tool_response('delegate', {'task': 'inspect', 'max_steps': 2}),
        text_response('child'), text_response('parent'),
    ])
    assert agent.ask("inspect") == "parent"
    assert agent.turn_usage["calls"] == 3
    assert agent.session["usage"]["totals"]["input_tokens"] == 300
    resumed = agent_at(tmp_path, [text_response('ok')], session=agent.session_store.load(agent.session["id"]))
    resumed.ask("again")
    assert resumed.turn_usage["calls"] == 1
    assert resumed.session["usage"]["calls"] == 4
    resumed.reset()
    assert resumed.session["usage"] == empty_usage()
    assert resumed.turn_usage == empty_usage()


def test_retry_and_failed_request_usage(tmp_path):
    agent = agent_at(tmp_path, [ModelResponse(), text_response('ok')])
    agent.ask("hello")
    assert agent.turn_usage["calls"] == 2
    agent.model_client = Client([RuntimeError("failed")])
    with pytest.raises(RuntimeError):
        agent.ask("again")
    assert agent.turn_usage["calls"] == 1
    assert agent.session["usage"]["calls"] == 3
    assert agent.context_usage["input_tokens"] == 100


def test_old_session_and_reasoning(tmp_path):
    agent = agent_at(tmp_path, [])
    old = dict(agent.session)
    del old["usage"]
    resumed = agent_at(tmp_path, [], session=old)
    assert resumed.session["usage"]["historical_unknown"]
    assert reasoning_status(Client([])) == ("关闭", "—")
    assert reasoning_status(object()) == ("默认", "—")
    client = Client([])
    client.thinking = {"type": "enabled", "effort": "high"}
    assert reasoning_status(client) == ("开启", "high")


def test_missing_usage_does_not_reuse_previous_counts(tmp_path):
    class MissingClient(Client):
        def complete(self, *args, **kwargs):
                return text_response('ok')

    agent = agent_at(tmp_path, [text_response('ok')])
    agent.ask("first")
    agent.model_client = MissingClient([])
    agent.ask("second")
    assert count_text(agent.turn_usage, "input_tokens") == "未知"
    assert count_text(agent.session["usage"], "input_tokens") == "100(不完整)"
    assert agent.context_usage.get("input_tokens") is None
    assert "（估算）" in status_lines(agent.turn_usage, agent.session["usage"], agent.model_client,
                                    agent.context_usage, agent.context_window)[-1]
