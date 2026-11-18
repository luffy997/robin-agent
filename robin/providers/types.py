"""与供应商无关的模型消息和调用接口。"""

from dataclasses import asdict, dataclass, field
from typing import Protocol
from uuid import uuid4


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = field(default_factory=lambda: "call_" + uuid4().hex)


@dataclass
class ToolResult:
    tool_call_id: str
    name: str
    content: str


@dataclass
class ModelRequest:
    messages: list[dict]
    tools: list[dict]
    max_output_tokens: int
    prompt_cache_key: str | None = None
    finalization: bool = False
    context_text: str = ""


@dataclass
class ModelResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str | None = None
    stop_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)
    metadata: dict = field(default_factory=dict)
    replay: dict = field(default_factory=dict)
    prior_usages: list[Usage] = field(default_factory=list)

    def usage_metadata(self):
        return {**self.metadata, **asdict(self.usage)}


class ModelError(RuntimeError):
    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...


def text_response(text):
    return ModelResponse(text=text)


def tool_response(name, arguments):
    return ModelResponse(tool_calls=[ToolCall(name, arguments)], stop_reason="tool_use")
