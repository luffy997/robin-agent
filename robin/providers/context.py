"""构建原生工具消息，裁剪时保留完整调用和结果组。"""

import ast
import json

from .types import ModelRequest
from .limits import output_budget


def tool_definitions(tools):
    definitions = []
    for name, spec in tools.items():
        properties, required = {}, []
        for key, declaration in spec["schema"].items():
            kind, sep, default = declaration.partition("=")
            properties[key] = {"type": {"str": "string", "int": "integer"}[kind]}
            if sep:
                properties[key]["default"] = ast.literal_eval(default)
            else:
                required.append(key)
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec["description"],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
        )
    return definitions


def build_request(agent, prompt, metadata, finalization=False):
    # 上下文管理器继续控制记忆及系统上下文；历史只通过结构化消息发送。
    system = getattr(agent.context_manager, "model_system_text", None)
    if system is None:
        system = prompt.split("\n\nTranscript:", 1)[0].split(
            "\n\nCurrent user request:", 1
        )[0]
    legacy = agent.session.get("legacy_summary", "")
    if legacy:
        system += "\n\n" + legacy
    groups = []
    for item in agent.session["history"]:
        role = item["role"]
        if role == "tool":
            if groups and item.get("tool_call_id"):
                groups[-1].append(
                    {
                        "role": "tool",
                        "tool_call_id": item["tool_call_id"],
                        "content": item["content"],
                    }
                )
            continue
        message = {"role": role, "content": item.get("content", "")}
        if item.get("tool_calls"):
            message["tool_calls"] = item["tool_calls"]
        message.update(item.get("replay", {}))
        groups.append([message])
    # 中断时可能尚未写入所有结果，不把孤立调用送入后端。
    valid = []
    for group in groups:
        calls = group[0].get("tool_calls", [])
        if calls and {c["id"] for c in calls} != {m["tool_call_id"] for m in group[1:]}:
            continue
        valid.append(group)
    budget = metadata.get("section_budgets", {}).get("history", 5200)
    selected, size = [], 0
    for group in reversed(valid):
        group = [dict(message) for message in group]
        # Bound large results without changing call IDs, arguments or replay signatures.
        result_budget = max(80, budget // max(1, len(group) - 1) - 200)
        for message in group[1:]:
            if len(message["content"]) > result_budget:
                message["content"] = (
                    message["content"][:result_budget] + "\n[tool result truncated]"
                )
        cost = len(json.dumps(group, ensure_ascii=False))
        if size + cost > budget and selected:
            break
        selected.insert(0, group)
        size += cost
    messages = [{"role": "system", "content": system}]
    # Keep the current user objective even when a large tool group fills the history budget.
    current_user = next((g for g in reversed(valid) if g[0]["role"] == "user"), None)
    if current_user and current_user not in selected:
        selected.insert(0, current_user)
    messages.extend(message for group in selected for message in group)
    if finalization:
        messages.append(
            {
                "role": "user",
                "content": "Tool budget exhausted. Summarize the available evidence without calling tools.",
            }
        )
    definitions = tool_definitions(agent.tools)
    limit, estimated_input = output_budget(messages, definitions, agent.max_new_tokens, agent.context_window)
    metadata.update({"requested_output_tokens": agent.max_new_tokens,
                     "effective_output_tokens": limit, "estimated_input_tokens": estimated_input,
                     "context_window": agent.context_window})
    return ModelRequest(
        messages,
        definitions,
        limit,
        metadata.get("prompt_cache_key")
        if agent.feature_enabled("prompt_cache")
        else None,
        finalization,
        prompt,
    )
