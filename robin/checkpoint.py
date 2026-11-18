"""Checkpoint and resume-state helpers."""

import uuid

from .features import memory as memorylib
from .workspace import clip, now

# checkpoint payload 的结构版本；只要字段语义或恢复校验规则不兼容，就递增它。
CHECKPOINT_SCHEMA_VERSION = "phase1-v1"
# 当前 session 没有可用 checkpoint，恢复逻辑会按全新任务启动。
CHECKPOINT_NONE_STATUS = "no-checkpoint"
# checkpoint 的 schema、运行环境和关键文件新鲜度都通过校验，可以完整复用。
CHECKPOINT_FULL_VALID_STATUS = "full-valid"
# checkpoint 可读，但部分关键文件已经变化；prompt 会提示模型重新锚定这些文件。
CHECKPOINT_PARTIAL_STALE_STATUS = "partial-stale"
# 当前运行环境和 checkpoint 记录的 runtime identity 不一致，需要谨慎续跑。
CHECKPOINT_WORKSPACE_MISMATCH_STATUS = "workspace-mismatch"
# checkpoint 来自不兼容的 schema 版本，恢复逻辑不能信任其结构。
CHECKPOINT_SCHEMA_MISMATCH_STATUS = "schema-mismatch"

RUNTIME_IDENTITY_KEYS = (
    "cwd",
    "model",
    "model_client",
    "approval_policy",
    "read_only",
    "max_steps",
    "max_new_tokens",
    "feature_flags",
    "shell_env_allowlist",
    "workspace_fingerprint",
    "tool_signature",
)


def current_runtime_identity(agent):
    # checkpoint 不只保存“做到了哪里”，也保存当时的运行环境。
    # 这样恢复时可以判断：同一个 session 是否已经换了 cwd、模型、工具集或安全策略。
    """生成用于恢复校验的当前运行环境指纹。"""
    return {
        "session_id": agent.session.get("id", ""),
        "cwd": str(agent.root),
        "model": str(getattr(agent.model_client, "model", "")),
        "model_client": agent.model_client.__class__.__name__,
        "approval_policy": agent.approval_policy,
        "read_only": bool(agent.read_only),
        "max_steps": int(agent.max_steps),
        "max_new_tokens": int(agent.max_new_tokens),
        "feature_flags": dict(agent.feature_flags),
        "shell_env_allowlist": list(agent.shell_env_allowlist),
        "workspace_fingerprint": getattr(getattr(agent, "prefix_state", None), "workspace_fingerprint", agent.workspace.fingerprint()),
        "tool_signature": agent.tool_signature(),
    }


def checkpoint_state(agent):
    """返回 session 中的 checkpoint 容器。"""
    agent._ensure_session_shape()
    return agent.session["checkpoints"]


def current_checkpoint(agent):
    """读取当前 session 指向的最新 checkpoint。"""
    state = checkpoint_state(agent)
    checkpoint_id = str(state.get("current_id", "")).strip()
    if not checkpoint_id:
        return None
    return state.get("items", {}).get(checkpoint_id)


def evaluate_resume_state(agent):
    """判断当前运行是否能安全复用上次 checkpoint。"""
    previous_resume_state = dict(agent.session.get("resume_state", {}) or {})
    invalidated = agent.invalidate_stale_memory()
    checkpoint = current_checkpoint(agent)
    status = CHECKPOINT_NONE_STATUS
    stale_paths = list(invalidated)
    mismatch_fields = []
    if checkpoint:
        # 恢复判断分两层：
        # 1. key_files 的 freshness 变了，说明“读过/依赖过的文件”可能过期；
        # 2. runtime_identity 变了，说明工具、模型或安全策略可能已经不是同一套。
        # 前者通常还能继续，只是需要重新确认文件；后者更像运行环境不匹配。
        if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            status = CHECKPOINT_SCHEMA_MISMATCH_STATUS
        else:
            for item in checkpoint.get("key_files", []):
                path = str(item.get("path", "")).strip()
                if not path:
                    continue
                expected = item.get("freshness")
                current = memorylib.file_freshness(path, agent.root)
                if expected != current and path not in stale_paths:
                    stale_paths.append(path)
            saved_identity = dict(checkpoint.get("runtime_identity", {}) or agent.session.get("runtime_identity", {}) or {})
            current_identity = current_runtime_identity(agent)
            for key in RUNTIME_IDENTITY_KEYS:
                if key not in saved_identity:
                    continue
                if saved_identity.get(key) != current_identity.get(key):
                    mismatch_fields.append(key)
            mismatch_fields.sort()
            if stale_paths:
                status = CHECKPOINT_PARTIAL_STALE_STATUS
            elif mismatch_fields:
                status = CHECKPOINT_WORKSPACE_MISMATCH_STATUS
            else:
                status = CHECKPOINT_FULL_VALID_STATUS

    resume_state = {
        "status": status,
        "stale_paths": stale_paths,
        "runtime_identity_mismatch_fields": mismatch_fields,
        "stale_summary_invalidations": max(
            len(invalidated),
            int(previous_resume_state.get("stale_summary_invalidations", 0))
            if status == CHECKPOINT_PARTIAL_STALE_STATUS
            else 0,
        ),
    }
    # resume_state 会进入下一轮 prompt，让模型知道哪些旧上下文可以信、
    # 哪些文件或运行假设需要重新验证。
    agent.session["resume_state"] = resume_state
    agent.session["runtime_identity"] = current_runtime_identity(agent)
    return resume_state


def render_checkpoint_text(agent):
    """把 checkpoint 渲染成可放入 prompt 的续跑摘要。"""
    checkpoint = current_checkpoint(agent)
    if not checkpoint:
        return ""
    # 这段文本会被拼进 prompt prefix；它是给模型看的“续跑摘要”，
    # 不是完整审计记录。完整过程仍然在 trace/report/task_state 里。
    lines = [
        "Task checkpoint:",
        f"- Resume status: {agent.resume_state.get('status', CHECKPOINT_NONE_STATUS)}",
        f"- Current goal: {checkpoint.get('current_goal', '-') or '-'}",
        f"- Current blocker: {checkpoint.get('current_blocker', '-') or '-'}",
        f"- Next step: {checkpoint.get('next_step', '-') or '-'}",
    ]
    key_files = [str(item.get("path", "")).strip() for item in checkpoint.get("key_files", []) if str(item.get("path", "")).strip()]
    lines.append(f"- Key files: {', '.join(key_files) or '-'}")
    if checkpoint.get("completed"):
        lines.append("- Completed: " + " | ".join(str(item) for item in checkpoint.get("completed", [])))
    if checkpoint.get("excluded"):
        lines.append("- Excluded: " + " | ".join(str(item) for item in checkpoint.get("excluded", [])))
    if agent.resume_state.get("stale_paths"):
        lines.append("- Stale paths: " + ", ".join(agent.resume_state["stale_paths"]))
    summary = str(checkpoint.get("summary", "")).strip()
    if summary:
        lines.append(f"- Summary: {summary}")
    return "\n".join(lines)


def infer_next_step(task_state):
    """根据任务状态推断恢复后的下一步提示。"""
    if task_state.status == "completed":
        return "No next step recorded."
    if task_state.stop_reason == "step_limit_reached":
        return "Resume from the latest checkpoint and continue the task."
    if task_state.last_tool:
        return f"Decide the next action after {task_state.last_tool}."
    return "Continue the task from the latest checkpoint."


def create_checkpoint(agent, task_state, user_message, trigger):
    """创建 checkpoint 并写回 session。"""
    state = checkpoint_state(agent)
    current = current_checkpoint(agent)
    checkpoint_id = "ckpt_" + uuid.uuid4().hex[:8]
    key_files = []
    freshness = {}
    # 只跟踪 working memory 里最近触达的关键文件。
    # 这比给整个仓库打快照便宜，也更贴近“继续上次任务”真正依赖的上下文。
    for path in agent.memory.to_dict()["working"]["recent_files"]:
        file_freshness = memorylib.file_freshness(path, agent.root)
        freshness[path] = file_freshness
        key_files.append({"path": path, "freshness": file_freshness})
    checkpoint = {
        "checkpoint_id": checkpoint_id,
        "parent_checkpoint_id": current.get("checkpoint_id", "") if current else "",
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "created_at": now(),
        "current_goal": str(user_message),
        "completed": [task_state.final_answer] if task_state.final_answer else [],
        "excluded": [],
        "current_blocker": "" if str(task_state.stop_reason or "") in ("", "final_answer_returned") else str(task_state.stop_reason),
        "next_step": infer_next_step(task_state),
        "key_files": key_files,
        "freshness": freshness,
        "summary": f"{trigger}: {clip(str(user_message), 120)}",
        "runtime_identity": current_runtime_identity(agent),
    }
    state["items"][checkpoint_id] = checkpoint
    state["current_id"] = checkpoint_id
    task_state.checkpoint_id = checkpoint_id
    agent.session["runtime_identity"] = checkpoint["runtime_identity"]
    agent.session_path = agent.session_store.save(agent.session)
    return checkpoint
