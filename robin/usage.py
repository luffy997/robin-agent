"""接口报告的用量统计；缺失计数保持未知。"""

FIELDS = ("input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens")


def empty_usage(historical_unknown=False):
    return {"calls": 0, "historical_unknown": historical_unknown,
            "totals": dict.fromkeys(FIELDS, 0), "known": dict.fromkeys(FIELDS, 0),
            "cache_input": 0, "cache_read": 0, "cache_calls": 0}


def add_usage(usage, metadata):
    usage["calls"] += 1
    for field in FIELDS:
        value = metadata.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            usage["totals"][field] += value
            usage["known"][field] += 1
    inputs, cached = metadata.get("input_tokens"), metadata.get("cached_tokens")
    if all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (inputs, cached)):
        usage["cache_input"] += inputs
        usage["cache_read"] += cached
        usage["cache_calls"] += 1


def count_text(usage, *fields):
    if usage["calls"] and any(usage["known"][f] == 0 for f in fields):
        return "未知"
    value = sum(usage["totals"][f] for f in fields)
    partial = usage["historical_unknown"] or any(usage["known"][f] < usage["calls"] for f in fields)
    return f"{value:,}" + ("(不完整)" if partial else "")


def reasoning_status(client):
    thinking = getattr(client, "thinking", None)
    if isinstance(thinking, dict):
        state = {"disabled": "关闭", "enabled": "开启", "adaptive": "自适应"}.get(thinking.get("type"), "默认")
        level = thinking.get("effort", thinking.get("budget_tokens", "—"))
    else:
        state = "关闭" if getattr(client, "thinking_disabled", False) else "默认"
        level = "—"
    return state, str(level)


def status_lines(turn, session, client):
    state, level = reasoning_status(client)
    rate = "未知"
    if turn["cache_calls"] and turn["cache_input"]:
        rate = f'{100 * turn["cache_read"] / turn["cache_input"]:.1f}%'
        if turn["cache_calls"] < turn["calls"]:
            rate += "(不完整)"
    return [
        f'本轮 Token 输入 {count_text(turn, "input_tokens")} / 输出 {count_text(turn, "output_tokens")} | 会话累计 {count_text(session, "input_tokens", "output_tokens")}',
        f'缓存读取 {count_text(turn, "cached_tokens")} / 命中率 {rate} | 推理 {state} / 等级 {level}',
    ]
