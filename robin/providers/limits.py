"""Local model limits and conservative request budgeting; no remote catalog lookup."""

import json
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


def output_budget(messages, tools, requested, context_window):
    estimated_input = estimate_input(messages, tools)
    available = context_window - estimated_input - CONTEXT_SAFETY_TOKENS
    if available < 1:
        raise ValueError(
            "Model context budget exhausted before request; shorten/reset the session "
            "or configure a larger --context-window supported by your model."
        )
    return min(requested, available), estimated_input


def estimate_input(messages, tools=()):
    """本地保守估计：ASCII 约三字符一个 token，非 ASCII 每字符两个；不计为接口用量。"""
    serialized = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
    ascii_chars = sum(ord(char) < 128 for char in serialized)
    return math.ceil(ascii_chars / 3) + 2 * (len(serialized) - ascii_chars) + 32 * len(messages)
