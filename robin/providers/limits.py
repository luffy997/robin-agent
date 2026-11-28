"""Local model limits and conservative request budgeting; no remote catalog lookup."""

import json
import logging
import math
from pathlib import Path

DEFAULT_MAX_NEW_TOKENS = 16384
DEFAULT_CONTEXT_WINDOW = 128000
CONTEXT_SAFETY_TOKENS = 4096


def positive_integer(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0 or str(parsed) != str(value):
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def model_limits(root, provider, model):
    path = Path(root) / "robin.models.json"
    if not path.exists():
        return DEFAULT_MAX_NEW_TOKENS, DEFAULT_CONTEXT_WINDOW
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
        entry = catalog.get(provider, {}).get(model, {})
        output = positive_integer(entry.get("maxTokens", DEFAULT_MAX_NEW_TOKENS), "maxTokens")
        context = positive_integer(entry.get("contextWindow", DEFAULT_CONTEXT_WINDOW), "contextWindow")
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"Invalid model limits in {path}: {exc}") from exc
    return output, context


def output_budget(messages, tools, requested, context_window, *, model=None):
    estimated_input = estimate_input(messages, tools, model=model)
    available = context_window - estimated_input - CONTEXT_SAFETY_TOKENS
    if available < 1:
        raise ValueError(
            "Model context budget exhausted before request; shorten/reset the session "
            "or configure a larger --context-window supported by your model."
        )
    return min(requested, available), estimated_input


def estimate_input(messages, tools=(), *, model=None):
    """优先按模型 tokenizer 估算；无模型或计数失败时退回保守字符预算。"""
    if model:
        from .clients import _library

        try:
            count = _library().token_counter(
                model=model, messages=messages, tools=list(tools) or None,
                use_default_image_token_count=True,
            )
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                return count
        except Exception:
            # 计数不可用不能阻断任务，也不能把禁用计数时的零当成空上下文。
            pass
        logging.getLogger(__name__).warning("LiteLLM token counting unavailable; using character estimate")
    serialized = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
    ascii_chars = sum(ord(char) < 128 for char in serialized)
    return math.ceil(ascii_chars / 3) + 2 * (len(serialized) - ascii_chars) + 32 * len(messages)
