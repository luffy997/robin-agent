"""构建原生工具消息，裁剪时保留完整调用和结果组。"""

import ast

from .types import ModelRequest
from .limits import output_budget
from ..history_compaction import checkpoint_messages, compact_history


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


def build_request(agent, prompt, metadata, finalization=False, force_compaction=False):
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
    through = agent.session.get("history_compaction", {}).get("through", -1)
    for index, item in enumerate(agent.session["history"]):
        if index <= through:
            continue
        role = item["role"]
        if role == "tool":
            if groups and item.get("tool_call_id"):
                groups[-1][1].append(
                    {
                        "role": "tool",
                        "tool_call_id": item["tool_call_id"],
                        "content": item["content"],
                    }
                )
                groups[-1][0] = index
            continue
        message = {"role": role, "content": item.get("content", "")}
        if item.get("tool_calls"):
            message["tool_calls"] = item["tool_calls"]
        message.update(item.get("replay", {}))
        groups.append([index, [message]])
    # 中断时可能尚未写入所有结果，不把孤立调用送入后端。
    valid = []
    for end, group in groups:
        calls = group[0].get("tool_calls", [])
        if calls and {c["id"] for c in calls} != {m["tool_call_id"] for m in group[1:]}:
            continue
        valid.append((end, group))
    definitions = [] if finalization else tool_definitions(agent.tools)
    executor = getattr(agent, "tool_executor", None)
    retained = executor.read_context() if executor is not None else ""
    selected = compact_history(agent, system, valid, definitions, metadata, agent.redact_text(retained),
                               force=force_compaction)
    messages = [{"role": "system", "content": system}, *checkpoint_messages(agent)]
    if metadata["history_compacted"]:
        retained = ""
    if retained:
        messages.append({"role": "user", "content": agent.redact_text(retained)})
    current_user = next(({"role": "user", "content": item["content"]}
                         for item in reversed(agent.session["history"]) if item["role"] == "user"), None)
    if current_user and current_user not in selected:
        selected.insert(0, current_user)
    messages.extend(selected)
    if finalization:
        messages.append(
            {
                "role": "user",
                "content": "Tool budget exhausted. Summarize the available evidence without calling tools.",
            }
        )
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
