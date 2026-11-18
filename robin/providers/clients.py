"""LiteLLM 负责供应商协议；运行时只接收统一响应。"""

import json
import os
import time

from .types import ModelError, ModelRequest, ModelResponse, ToolCall, Usage

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_TELEMETRY", "False")
os.environ["LITELLM_MODE"] = "PRODUCTION"

APIS = ("chat-completions", "responses", "anthropic-messages", "ollama-chat")


def _library():
    import litellm

    litellm.telemetry = False
    litellm.drop_params = False
    litellm.suppress_debug_info = True
    return litellm


def _dict(value):
    return (
        value.model_dump(exclude_none=True)
        if hasattr(value, "model_dump")
        else dict(value or {})
    )


def _anthropic_transport(timeout):
    from litellm.llms.custom_httpx.http_handler import HTTPHandler

    class CompatibleAnthropicTransport(HTTPHandler):
        def post(self, url, data=None, **kwargs):
            # LiteLLM 1.110 emits an optional type=custom discriminator which
            # DeepSeek's Messages implementation rejects. Omitting it is valid
            # Anthropic function-tool syntax; all other conversion stays in LiteLLM.
            if isinstance(data, str):
                body = json.loads(data)
                for tool in body.get("tools", []):
                    if tool.get("type") == "custom":
                        tool.pop("type")
                data = json.dumps(body)
            return super().post(url, data=data, **kwargs)

    return CompatibleAnthropicTransport(timeout=timeout)


def reported_usage(data, api):
    usage = data.get("usage") or {}
    if api == "ollama-chat":
        return Usage(data.get("prompt_eval_count"), data.get("eval_count"))
    if api == "anthropic-messages":
        ordinary = usage.get("input_tokens")
        read, write = (
            usage.get("cache_read_input_tokens"),
            usage.get("cache_creation_input_tokens"),
        )
        total = ordinary + (read or 0) + (write or 0) if ordinary is not None else None
        return Usage(total, usage.get("output_tokens"), read, write)
    details = (
        usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    )
    return Usage(
        usage.get("prompt_tokens", usage.get("input_tokens")),
        usage.get("completion_tokens", usage.get("output_tokens")),
        details.get("cached_tokens"),
        usage.get("cache_creation_input_tokens"),
    )


def normalize_response(value):
    data = _dict(value)
    usage = data.get("usage") or {}
    details = (
        usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    )
    cache = details.get("cached_tokens", usage.get("cache_read_input_tokens"))
    response = ModelResponse(
        usage=Usage(
            input_tokens=usage.get("prompt_tokens", usage.get("input_tokens")),
            output_tokens=usage.get("completion_tokens", usage.get("output_tokens")),
            cached_tokens=cache,
            cache_write_tokens=usage.get("cache_creation_input_tokens"),
        )
    )
    choices = data.get("choices") or []
    if not choices:
        response.stop_reason = "error"
        return response
    choice = choices[0]
    message = choice.get("message") or {}
    response.text = message.get("content") or ""
    response.reasoning = message.get("reasoning_content")
    response.replay = {
        k: message[k]
        for k in (
            "reasoning_content",
            "reasoning_items",
            "thinking_blocks",
            "provider_specific_fields",
        )
        if k in message
    }
    response.stop_reason = {
        "tool_calls": "tool_use",
        "stop": "stop",
        "length": "length",
    }.get(choice.get("finish_reason"), "error")
    seen = set()
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments", "{}")
        try:
            arguments = (
                json.loads(arguments) if isinstance(arguments, str) else arguments
            )
        except ValueError as exc:
            raise ModelError("Model returned invalid tool arguments", response) from exc
        if (
            not isinstance(arguments, dict)
            or not call.get("id")
            or call["id"] in seen
            or not function.get("name")
        ):
            raise ModelError(
                "Model returned invalid tool call IDs or arguments", response
            )
        seen.add(call["id"])
        response.tool_calls.append(ToolCall(function["name"], arguments, call["id"]))
    response.metadata = {
        "stop_reason": response.stop_reason,
        "cache_hit": cache > 0 if cache is not None else None,
    }
    return response


class LiteLLMModelClient:
    def __init__(
        self,
        model,
        base_url,
        api_key=None,
        temperature=None,
        timeout=300,
        api="chat-completions",
        thinking=None,
        reasoning_disabled=False,
        **kwargs,
    ):
        if api not in APIS:
            raise ValueError(f"Unsupported API: {api}")
        self.model, self.base_url, self.api_key = model, base_url.rstrip("/"), api_key
        self.temperature, self.timeout, self.api = temperature, timeout, api
        if kwargs:
            raise ValueError(
                f"Unsupported model parameters: {', '.join(sorted(kwargs))}"
            )
        self.top_p = None
        if thinking is not None and api != "anthropic-messages":
            raise ValueError("thinking configuration requires --api anthropic-messages")
        self.thinking = ({"type": "disabled"} if reasoning_disabled and api == "anthropic-messages" else thinking)
        self.thinking_disabled = reasoning_disabled or api == "ollama-chat"
        self.supports_prompt_cache = api == "responses"
        self._transport = None

    def complete(self, request: ModelRequest) -> ModelResponse:
        library = _library()
        routes = {
            "chat-completions": "openai/",
            "responses": "openai/responses/",
            "anthropic-messages": "anthropic/",
            "ollama-chat": "ollama_chat/",
        }
        base_url = self.base_url
        if self.api == "anthropic-messages" and not base_url.endswith("/messages"):
            base_url += "/messages" if base_url.endswith("/v1") else "/v1/messages"
        if self.api == "ollama-chat":
            # Register local capabilities so LiteLLM does not probe /api/show on localhost.
            # register_model itself probes unknown Ollama models in this pinned version.
            library.model_cost.setdefault(
                "ollama_chat/" + self.model,
                {
                    "litellm_provider": "ollama_chat",
                    "mode": "chat",
                    "supports_function_calling": True,
                },
            )
        options = {
            "model": routes[self.api] + self.model,
            "messages": request.messages,
            "api_base": base_url,
            "api_key": self.api_key,
            "max_tokens": request.max_output_tokens,
            "timeout": self.timeout,
            "num_retries": 0,
            "max_retries": 0,
            "stream": False,
            "drop_params": False,
        }
        # Pin the selected protocol; never let a model-name heuristic switch endpoints.
        if self.api == "chat-completions":
            options["_skip_responses_api_bridge"] = True
        if self.temperature is not None:
            options["temperature"] = self.temperature
        if self.top_p is not None:
            options["top_p"] = self.top_p
        if request.tools and not (self.api == "ollama-chat" and request.finalization):
            options["tools"] = request.tools
            if self.api != "ollama-chat":
                options["tool_choice"] = "none" if request.finalization else "auto"
        if self.thinking is not None:
            options["thinking"] = self.thinking
            # Custom Anthropic gateways may use non-Claude model identifiers.
            options["allowed_openai_params"] = ["thinking"]
        if self.api == "ollama-chat":
            options["think"] = False
        elif self.thinking_disabled and self.api == "chat-completions":
            # DeepSeek 的 Chat 接口使用扩展字段，不能直接传 Anthropic 参数。
            options["extra_body"] = {"thinking": {"type": "disabled"}}
        elif self.thinking_disabled and self.api == "responses":
            options["extra_body"] = {"reasoning": {"effort": "none"}}
        if self.api == "anthropic-messages" and self.api_key:
            options["extra_headers"] = {"Authorization": f"Bearer {self.api_key}"}
        if self.api == "anthropic-messages":
            if self._transport is None:
                self._transport = _anthropic_transport(self.timeout)
            options["client"] = self._transport
        if self.api == "responses" and request.prompt_cache_key:
            options["prompt_cache_key"] = request.prompt_cache_key
        raw = {}

        def capture(details):
            # LiteLLM fills missing counters with zero. Preserve what the API actually reported.
            value = details.get("original_response")
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    return
            if isinstance(value, dict):
                raw.update(value)

        from litellm.integrations.custom_logger import CustomLogger

        class UsageCapture(CustomLogger):
            def log_post_api_call(self, kwargs, response_obj, start_time, end_time):
                capture(kwargs)

        options["callbacks"] = [UsageCapture()]
        prior_usages = []
        for attempt in range(3):
            raw.clear()
            try:
                result = library.completion(**options)
                response = normalize_response(result)
                if raw:
                    response.usage = reported_usage(raw, self.api)
                    if self.api == "ollama-chat" and raw.get("done_reason") == "length":
                        response.stop_reason = "length"
                    response.metadata["stop_reason"] = response.stop_reason
                    cached = response.usage.cached_tokens
                    response.metadata["cache_hit"] = (
                        cached > 0 if cached is not None else None
                    )
                response.prior_usages = prior_usages
                return response
            except ModelError as exc:
                if exc.response:
                    if raw:
                        exc.response.usage = reported_usage(raw, self.api)
                    exc.response.prior_usages = prior_usages
                raise
            except Exception as exc:
                error_response = getattr(exc, "response", None)
                if error_response is not None:
                    try:
                        capture({"original_response": error_response.json()})
                    except (ValueError, AttributeError):
                        pass
                status = getattr(exc, "status_code", None)
                if attempt < 2 and (
                    status in {408, 429, 500, 502, 503, 504}
                    or isinstance(exc, TimeoutError)
                ):
                    prior_usages.append(reported_usage(raw, self.api))
                    time.sleep(0.25 * (2**attempt))
                    continue
                message = (
                    str(exc).replace(self.api_key, "[REDACTED]")
                    if self.api_key
                    else str(exc)
                )
                failure = ModelResponse(
                    usage=reported_usage(raw, self.api), prior_usages=prior_usages
                )
                raise ModelError(
                    f"{self.api} request failed (check --api and base URL): {message}",
                    failure,
                ) from exc


class OpenAICompatibleModelClient(LiteLLMModelClient):
    def __init__(self, **kwargs):
        kwargs.setdefault("api", "responses")
        super().__init__(**kwargs)


class AnthropicCompatibleModelClient(LiteLLMModelClient):
    def __init__(self, **kwargs):
        kwargs.setdefault("api", "anthropic-messages")
        super().__init__(**kwargs)


class OllamaModelClient(LiteLLMModelClient):
    def __init__(
        self, model, host, temperature=None, top_p=None, timeout=300, **kwargs
    ):
        self.host = host
        kwargs.setdefault("api", "ollama-chat")
        super().__init__(
            model, host, temperature=temperature, timeout=timeout, **kwargs
        )
        self.top_p = top_p


class FakeModelClient:
    supports_prompt_cache = False

    def __init__(self, outputs):
        self.outputs, self.prompts, self.requests = list(outputs), [], []

    def complete(self, request):
        self.requests.append(request)
        self.prompts.append(request.context_text)
        if not self.outputs:
            raise ModelError("fake model ran out of outputs")
        result = self.outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        if not isinstance(result, ModelResponse):
            raise TypeError("FakeModelClient requires ModelResponse fixtures")
        return result
