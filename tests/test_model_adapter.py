"""Exercise the real LiteLLM transformations with an in-memory HTTP transport."""

import json

import httpx
import pytest

from robin import ModelRequest, Usage
from robin.providers.clients import (
    APIS,
    LiteLLMModelClient,
    normalize_response,
    reported_usage,
)
from robin.providers.context import tool_definitions
from robin.providers.types import ModelError
from robin.tools import BASE_TOOL_SPECS


RAW = {
    "chat-completions": {
        "id": "chat-test",
        "object": "chat.completion",
        "created": 1,
        "model": "gateway-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "private reasoning",
                    "tool_calls": [
                        {
                            "id": "call_test",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"README.md"}',
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 40},
        },
    },
    "responses": {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "model": "gateway-model",
        "status": "completed",
        "output": [
            {
                "type": "reasoning",
                "id": "rs_test",
                "summary": [{"type": "summary_text", "text": "private reasoning"}],
                "encrypted_content": "opaque",
            },
            {
                "type": "function_call",
                "id": "fc_test",
                "call_id": "call_test",
                "name": "read_file",
                "arguments": '{"path":"README.md"}',
                "status": "completed",
            },
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_tokens_details": {"cached_tokens": 40},
        },
    },
    "anthropic-messages": {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "gateway-model",
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "content": [
            {
                "type": "thinking",
                "thinking": "private reasoning",
                "signature": "signature-test",
            },
            {
                "type": "tool_use",
                "id": "call_test",
                "name": "read_file",
                "input": {"path": "README.md"},
            },
        ],
        "usage": {
            "input_tokens": 50,
            "output_tokens": 20,
            "cache_read_input_tokens": 40,
            "cache_creation_input_tokens": 10,
        },
    },
    "ollama-chat": {
        "model": "gateway-model",
        "created_at": "2026-01-01T00:00:00Z",
        "done": True,
        "done_reason": "stop",
        "message": {
            "role": "assistant",
            "content": "",
            "thinking": "private reasoning",
            "tool_calls": [
                {"function": {"name": "read_file", "arguments": {"path": "README.md"}}}
            ],
        },
        "prompt_eval_count": 100,
        "eval_count": 20,
    },
}


def request(finalization=False):
    return ModelRequest(
        [
            {"role": "system", "content": "You are a test agent."},
            {"role": "user", "content": "read README.md"},
        ],
        tool_definitions(BASE_TOOL_SPECS),
        512,
        finalization=finalization,
    )


@pytest.mark.parametrize(
    "api,path",
    [
        ("chat-completions", "/v1/chat/completions"),
        ("responses", "/v1/responses"),
        ("anthropic-messages", "/v1/messages"),
        ("ollama-chat", "/api/chat"),
    ],
)
def test_real_litellm_protocol_conversion(monkeypatch, api, path):
    sent = []

    def send(client, req, *args, **kwargs):
        sent.append(req)
        return httpx.Response(200, json=RAW[api], request=req)

    monkeypatch.setattr(httpx.Client, "send", send)
    client = LiteLLMModelClient(
        "gateway-model",
        "https://gateway.invalid" + ("" if api == "ollama-chat" else "/v1"),
        "test-secret",
        api=api,
    )
    response = client.complete(request())
    assert len(sent) == 1
    assert sent[0].url.path == path
    body = json.loads(sent[0].content)
    assert body["model"] == "gateway-model"
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "README.md"}
    assert response.tool_calls[0].id
    assert response.stop_reason == "tool_use"
    assert response.reasoning == "private reasoning"
    assert response.text == ""
    assert response.usage.input_tokens == 100
    assert response.usage.output_tokens == 20
    assert response.usage.cached_tokens == (None if api == "ollama-chat" else 40)
    if api == "anthropic-messages":
        assert response.usage.cache_write_tokens == 10
        assert sent[0].headers["Authorization"] == "Bearer test-secret"
        assert sent[0].headers["x-api-key"] == "test-secret"
        assert response.replay["thinking_blocks"][0]["signature"] == "signature-test"
    elif api != "ollama-chat":
        assert sent[0].headers["Authorization"] == "Bearer test-secret"
    if api == "ollama-chat":
        assert body["think"] is False


@pytest.mark.parametrize("api", APIS)
def test_reported_missing_counters_stay_unknown(api):
    assert reported_usage({}, api) == Usage()


def test_schema_and_invalid_calls():
    schema = tool_definitions(BASE_TOOL_SPECS)[1]["function"]["parameters"]
    assert schema["required"] == ["path"]
    assert schema["properties"]["start"] == {"type": "integer", "default": 1}
    data = json.loads(json.dumps(RAW["chat-completions"]))
    data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "not json"
    with pytest.raises(ModelError) as caught:
        normalize_response(data)
    assert caught.value.response.usage.input_tokens == 100


def test_errors_redacted_and_no_endpoint_fallback(monkeypatch):
    sent = []

    def send(client, req, *args, **kwargs):
        sent.append(req)
        return httpx.Response(
            401,
            json={
                "error": {"message": "bad test-secret", "type": "authentication_error"}
            },
            request=req,
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    client = LiteLLMModelClient(
        "gateway-model", "https://gateway.invalid/v1", "test-secret"
    )
    with pytest.raises(ModelError) as caught:
        client.complete(request())
    assert "test-secret" not in str(caught.value)
    assert "--api" in str(caught.value)
    assert len(sent) == 1


def test_unsupported_parameters_fail():
    with pytest.raises(ValueError, match="Unsupported model parameters"):
        LiteLLMModelClient("m", "https://gateway.invalid", unsupported=True)
    with pytest.raises(ValueError, match="thinking configuration"):
        LiteLLMModelClient(
            "m", "https://gateway.invalid", thinking={"type": "disabled"}
        )


@pytest.mark.parametrize("api", APIS)
def test_text_and_missing_usage_through_litellm(monkeypatch, api):
    data = json.loads(json.dumps(RAW[api]))
    if api == "chat-completions":
        data["choices"][0]["message"] = {
            "role": "assistant",
            "content": "ordinary <tool> example",
        }
        data["choices"][0]["finish_reason"] = "stop"
    elif api == "responses":
        data["output"] = [
            {
                "type": "message",
                "id": "msg_text",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "ordinary <tool> example",
                        "annotations": [],
                    }
                ],
            }
        ]
    elif api == "anthropic-messages":
        data["content"] = [{"type": "text", "text": "ordinary <tool> example"}]
        data["stop_reason"] = "end_turn"
    else:
        data["message"] = {"role": "assistant", "content": "ordinary <tool> example"}
        data.pop("prompt_eval_count")
        data.pop("eval_count")
    data.pop("usage", None)
    if api == "anthropic-messages":
        data["usage"] = {}
    monkeypatch.setattr(
        httpx.Client,
        "send",
        lambda client, req, **kw: httpx.Response(200, json=data, request=req),
    )
    result = LiteLLMModelClient(
        "gateway-model", "https://gateway.invalid", "test-secret", api=api
    ).complete(request())
    assert result.text == "ordinary <tool> example"
    assert result.tool_calls == []
    assert result.stop_reason == "stop"
    assert result.usage == Usage()


@pytest.mark.parametrize("api", APIS)
def test_tool_result_and_reasoning_replay(monkeypatch, api):
    sent = []

    def send(client, req, **kwargs):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json=RAW[api], request=req)

    monkeypatch.setattr(httpx.Client, "send", send)
    client = LiteLLMModelClient(
        "gateway-model", "https://gateway.invalid", "test-secret", api=api
    )
    req = request()
    first = client.complete(req)
    call = first.tool_calls[0]
    req.messages.extend(
        [
            {
                "role": "assistant",
                "content": first.text,
                **first.replay,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": call.id, "content": "README contents"},
        ]
    )
    client.complete(req)
    assert len(sent) == 2
    body = sent[-1]
    if api == "anthropic-messages":
        blocks = body["messages"][-2]["content"]
        assert blocks[0]["signature"] == "signature-test"
        assert body["messages"][-1]["content"][0]["tool_use_id"] == call.id
    elif api == "responses":
        assert any(
            m.get("type") == "function_call_output" and m["call_id"] == call.id
            for m in body["input"]
        )
        assert any(m.get("encrypted_content") == "opaque" for m in body["input"])
    elif api == "chat-completions":
        assert body["messages"][-1]["tool_call_id"] == call.id
    else:
        assert body["messages"][-1]["role"] == "tool"
        assert body["messages"][-1]["content"] == "README contents"


def test_bounded_retry_usage_is_counted_once(monkeypatch, tmp_path):
    from robin import Robin, SessionStore, WorkspaceContext

    sent = []

    def send(client, req, **kwargs):
        sent.append(req)
        if len(sent) == 1:
            return httpx.Response(
                503,
                json={
                    "error": {"message": "unavailable"},
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1},
                },
                request=req,
            )
        data = json.loads(json.dumps(RAW["chat-completions"]))
        data["choices"][0]["message"] = {"role": "assistant", "content": "ok"}
        data["choices"][0]["finish_reason"] = "stop"
        return httpx.Response(200, json=data, request=req)

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr("robin.providers.clients.time.sleep", lambda _: None)
    client = LiteLLMModelClient(
        "gateway-model", "https://gateway.invalid/v1", "test-secret"
    )
    agent = Robin(
        client,
        WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / ".robin/sessions"),
    )
    assert agent.ask("hello") == "ok"
    assert len(sent) == 2
    assert agent.turn_usage["calls"] == 2
    assert agent.turn_usage["totals"]["input_tokens"] == 110
    assert agent.turn_usage["totals"]["output_tokens"] == 21


@pytest.mark.parametrize(
    "api,reason,expected",
    [
        ("chat-completions", "length", "length"),
        ("anthropic-messages", "max_tokens", "length"),
        ("ollama-chat", "length", "length"),
        ("responses", "incomplete", "length"),
    ],
)
def test_stop_reason_conversion(monkeypatch, api, reason, expected):
    data = json.loads(json.dumps(RAW[api]))
    if api == "chat-completions":
        data["choices"][0]["finish_reason"] = reason
    elif api == "anthropic-messages":
        data["stop_reason"] = reason
    elif api == "ollama-chat":
        data["done_reason"] = reason
    else:
        data["status"] = reason
        data["incomplete_details"] = {"reason": "max_output_tokens"}
    monkeypatch.setattr(
        httpx.Client,
        "send",
        lambda client, req, **kw: httpx.Response(200, json=data, request=req),
    )
    assert (
        LiteLLMModelClient(
            "gateway-model", "https://gateway.invalid", "test-secret", api=api
        )
        .complete(request())
        .stop_reason
        == expected
    )


def test_api_config_priority(monkeypatch):
    from robin.cli import _build_model_client, build_arg_parser

    monkeypatch.setenv("ROBIN_API", "chat-completions")
    args = build_arg_parser().parse_args(["--provider", "anthropic"])
    assert _build_model_client(args).api == "chat-completions"
    args = build_arg_parser().parse_args(
        ["--provider", "anthropic", "--api", "responses"]
    )
    assert _build_model_client(args).api == "responses"
    monkeypatch.delenv("ROBIN_API")
    defaults = {
        "openai": "responses",
        "anthropic": "anthropic-messages",
        "deepseek": "anthropic-messages",
        "ollama": "ollama-chat",
    }
    for provider, api in defaults.items():
        args = build_arg_parser().parse_args(["--provider", provider])
        assert _build_model_client(args).api == api


def test_deepseek_thinking_disabled_reaches_gateway(monkeypatch):
    sent = []

    def send(client, req, **kwargs):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json=RAW["anthropic-messages"], request=req)

    monkeypatch.setattr(httpx.Client, "send", send)
    client = LiteLLMModelClient(
        "deepseek-flash",
        "https://gateway.invalid/anthropic",
        "test-secret",
        api="anthropic-messages",
        thinking={"type": "disabled"},
    )
    client.complete(request())
    assert sent[0]["thinking"] == {"type": "disabled"}
    assert all("type" not in tool for tool in sent[0]["tools"])


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("source", ["cli", "env"])
def test_deepseek_protocol_override_preserves_reasoning_off(monkeypatch, api, source):
    from robin.cli import _build_model_client, build_arg_parser
    from robin.usage import reasoning_status

    sent = []
    def send(client, req, **kwargs):
        sent.append(req)
        return httpx.Response(200, json=RAW[api], request=req)
    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setenv("ROBIN_API", api if source == "env" else "anthropic-messages")
    monkeypatch.setenv("ROBIN_DEEPSEEK_API_KEY", "test-secret")
    argv = ["--provider", "deepseek", "--model", "gateway-model",
            "--base-url", "https://gateway.invalid" + ("" if api == "ollama-chat" else "/v1")]
    if source == "cli":
        argv.extend(["--api", api])
    client = _build_model_client(build_arg_parser().parse_args(argv))
    assert client.api == api
    assert reasoning_status(client) == ("关闭", "—")
    client.complete(request())
    assert len(sent) == 1
    body = json.loads(sent[0].content)
    if api in {"chat-completions", "anthropic-messages"}:
        assert body["thinking"] == {"type": "disabled"}
    elif api == "responses":
        assert body["reasoning"] == {"effort": "none"}
        assert "thinking" not in body
    else:
        assert body["think"] is False
        assert "thinking" not in body


def test_transient_failures_stop_after_three_attempts(monkeypatch):
    sent = []

    def send(client, req, **kwargs):
        sent.append(req)
        return httpx.Response(
            503, json={"error": {"message": "unavailable"}}, request=req
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr("robin.providers.clients.time.sleep", lambda _: None)
    client = LiteLLMModelClient(
        "gateway-model", "https://gateway.invalid/v1", "test-secret"
    )
    with pytest.raises(ModelError) as caught:
        client.complete(request())
    assert len(sent) == 3
    assert len(caught.value.response.prior_usages) == 2
    assert all(req.url.path == "/v1/chat/completions" for req in sent)
