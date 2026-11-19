"""Agent control loop extracted from the runtime facade."""

import json
import time
from dataclasses import asdict
from .usage import empty_usage
from .terminal import tool_activity
from .providers.context import build_request
from .providers.types import ModelError, ModelResponse, ToolResult

from .checkpoint import CHECKPOINT_NONE_STATUS, CHECKPOINT_PARTIAL_STALE_STATUS, CHECKPOINT_WORKSPACE_MISMATCH_STATUS
from .task_state import TaskState
from .workspace import clip, now


class AgentLoop:
    def __init__(self, agent):
        """初始化对象并保存运行所需依赖。"""
        # AgentLoop 不拥有状态本身；它只是 runtime facade 的流程编排层。
        self.agent = agent

    def _persist_model_failure(self, task_state, user_message, exc, run_started_at, prompt_metadata):
        """持久化模型调用失败时的状态、trace、checkpoint 和 report。"""
        agent = self.agent
        error_text = agent.redact_text(str(exc))
        final = f"Model request failed: {error_text}"
        task_state.stop_model_error(final)
        agent.run_store.write_task_state(task_state)
        # 模型请求失败也要生成 checkpoint：用户下次 resume 时能看到失败前的目标、
        # 关键文件和运行环境，而不是只拿到一个异常字符串。
        checkpoint = agent.create_checkpoint(task_state, user_message, trigger="model_error")
        agent.run_store.write_task_state(task_state)
        agent.emit_trace(
            task_state,
            "model_failed",
            {
                "error": error_text,
                "completion_metadata": dict(agent.last_completion_metadata),
            },
        )
        agent.emit_trace(
            task_state,
            "run_finished",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": final,
                "checkpoint_id": checkpoint["checkpoint_id"],
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        agent.last_prompt_metadata = dict(prompt_metadata)
        agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))

    def _request_model(self, task_state, user_message, prompt, prompt_metadata, run_started_at, purpose):
        """调用模型、收集 completion 元数据，并解析出工具调用或最终答案。"""
        agent = self.agent
        agent.emit_trace(
            task_state,
            "model_requested",
            {
                "attempts": task_state.attempts,
                "tool_steps": task_state.tool_steps,
                "prompt_cache_key": prompt_metadata.get("prompt_cache_key"),
                "purpose": purpose,
            },
        )
        model_started_at = time.monotonic()
        model_called = False
        try:
            request = build_request(agent, prompt, prompt_metadata, finalization=purpose == "finalization")
            model_called = True
            with tool_activity("model", label="Waiting for model response...", keep_result=False):
                response = agent.model_client.complete(request)
            if not isinstance(response, ModelResponse):
                raise ModelError("Model client must return ModelResponse")
        except Exception as exc:
            failure = getattr(exc, "response", None)
            for usage in failure.prior_usages if failure else []:
                agent.record_usage(asdict(usage))
            completion_metadata = failure.usage_metadata() if failure else {}
            if model_called:
                agent.record_usage(completion_metadata)
            prompt_metadata.update(completion_metadata)
            agent.last_completion_metadata = completion_metadata
            agent.last_prompt_metadata = prompt_metadata
            self._persist_model_failure(task_state, user_message, exc, run_started_at, prompt_metadata)
            raise RuntimeError(agent.redact_text(str(exc))) from exc
        completion_metadata = response.usage_metadata()
        for usage in response.prior_usages:
            agent.record_usage(asdict(usage))
        agent.record_usage(completion_metadata)
        prompt_metadata.update(completion_metadata)
        agent.last_completion_metadata = completion_metadata
        agent.last_prompt_metadata = prompt_metadata
        if response.stop_reason not in {"stop", "tool_use"}:
            message = f"Model response stopped: {response.stop_reason}"
            if response.stop_reason == "length":
                message += (
                    f" (output truncated; configured limit: {request.max_output_tokens} tokens). "
                    "No tools from this response were executed. "
                    "Restart with a larger --max-new-tokens value "
                    f"(for example, {max(8192, request.max_output_tokens * 2)}) "
                    "and --resume latest, then ask to continue. "
                    "If truncation persists, check the gateway/model output limit."
                )
            exc = ModelError(message, response)
            self._persist_model_failure(task_state, user_message, exc, run_started_at, prompt_metadata)
            raise exc
        if response.tool_calls:
            kind, payload = "tool", response.tool_calls
        elif response.text.strip() and response.stop_reason == "stop":
            kind, payload = "final", response.text
        else:
            kind, payload = "retry", "Return a non-empty answer or a valid native tool call."
        if response.reasoning:
            agent.emit_trace(task_state, "model_reasoning", {"reasoning": agent.redact_text(response.reasoning)})
        agent.emit_trace(
            task_state,
            "model_parsed",
            {
                "kind": kind,
                "completion_metadata": completion_metadata,
                "duration_ms": int((time.monotonic() - model_started_at) * 1000),
                "purpose": purpose,
            },
        )
        return response, kind, payload

    def _finish_success(self, task_state, user_message, final, run_started_at, response):
        """完成一次成功运行并写入历史、checkpoint、trace 和 report。"""
        agent = self.agent
        agent.record({"role": "assistant", "content": final, "reasoning": response.reasoning,
                      "replay": response.replay, "created_at": now()})
        task_state.finish_success(final)
        # 成功答案会尝试沉淀进 durable memory，供后续任务从长期记忆中召回。
        agent.promote_durable_memory(user_message, final)
        checkpoint = agent.create_checkpoint(task_state, user_message, trigger="run_finished")
        agent.run_store.write_task_state(task_state)
        agent.emit_trace(
            task_state,
            "checkpoint_created",
            {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "trigger": "run_finished",
            },
        )
        agent.emit_trace(
            task_state,
            "run_finished",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": final,
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
        return final

    def run(self, user_message):
        """执行当前对象负责的主流程。"""
        agent = self.agent
        run_started_at = time.monotonic()
        agent.turn_usage = empty_usage()
        agent.memory.set_task_summary(user_message)
        # 用户消息先入历史，再构建 task_state；后续 prompt 会看到本轮请求和之前上下文。
        agent.record({"role": "user", "content": user_message, "created_at": now()})

        task_state = TaskState.create(run_id=agent.new_run_id(), task_id=agent.new_task_id(), user_request=user_message)
        # resume_status 记录本轮是全新开始、完整续跑，还是带着过期/不匹配风险续跑。
        task_state.resume_status = agent.resume_state.get("status", CHECKPOINT_NONE_STATUS)
        agent.current_task_state = task_state
        agent.current_run_dir = agent.run_store.start_run(task_state)
        agent.emit_trace(
            task_state,
            "run_started",
            {
                "task_id": task_state.task_id,
                "user_request": clip(user_message, 300),
            },
        )

        tool_steps = 0
        attempts = 0
        max_attempts = max(agent.max_steps * 3, agent.max_steps + 4)

        # 这是 agent 的主循环，可以按“感知 -> 决策 -> 行动 -> 记录”来理解：
        # 1. 感知：重新组 prompt，把当前状态整理给模型看
        # 2. 决策：让模型返回一个工具调用，或一个最终答案
        # 3. 行动：如果是工具调用，就执行工具
        # 4. 记录：把结果写回 history / task_state / trace / memory
        # 然后进入下一轮，直到停机条件满足
        while tool_steps < agent.max_steps and attempts < max_attempts:
            attempts += 1
            task_state.record_attempt()
            agent.run_store.write_task_state(task_state)
            prompt_started_at = time.monotonic()
            prompt, prompt_metadata = agent._build_prompt_and_metadata(user_message)
            agent.emit_trace(
                task_state,
                "prompt_built",
                {
                    "prompt_metadata": prompt_metadata,
                    "duration_ms": int((time.monotonic() - prompt_started_at) * 1000),
                },
            )
            if prompt_metadata.get("resume_status") == CHECKPOINT_PARTIAL_STALE_STATUS:
                # 关键文件有变化时立即落 checkpoint，让 trace/report 明确标出重新锚定点。
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="freshness_mismatch")
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "freshness_mismatch",
                    },
                )
            elif prompt_metadata.get("resume_status") == CHECKPOINT_WORKSPACE_MISMATCH_STATUS:
                # runtime identity 不一致通常意味着 cwd、工具签名或配置变了，需要单独记录差异字段。
                agent.emit_trace(
                    task_state,
                    "runtime_identity_mismatch",
                    {
                        "fields": list(prompt_metadata.get("runtime_identity_mismatch_fields", [])),
                    },
                )
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="workspace_mismatch")
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "workspace_mismatch",
                    },
                )
            if prompt_metadata.get("budget_reductions"):
                # prompt 被预算压缩时也保存 checkpoint，方便复盘哪些上下文被裁剪后继续执行。
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="context_reduction")
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "context_reduction",
                    },
                )
            raw, kind, payload = self._request_model(
                task_state,
                user_message,
                prompt,
                prompt_metadata,
                run_started_at,
                purpose="action",
            )
            if kind == "tool":
                agent.record({"role": "assistant", "content": raw.text,
                              "tool_calls": [{"id": call.id, "type": "function", "function": {
                                  "name": call.name, "arguments": json.dumps(call.arguments)}} for call in payload],
                              "reasoning": raw.reasoning, "replay": raw.replay, "created_at": now()})
                for call in payload:
                    name, args = call.name, call.arguments
                    tool_started_at = time.monotonic()
                    if tool_steps >= agent.max_steps:
                        result, result_metadata = "Tool budget exhausted; call not executed.", {"budget_denied": True}
                    else:
                        tool_steps += 1
                        task_state.record_tool(name)
                        tool_result = agent.execute_tool(name, args)
                        result, result_metadata = tool_result.content, tool_result.metadata or {}
                    agent.record({"role": "tool", "args": args,
                                  **asdict(ToolResult(call.id, name, result)), "created_at": now()})
                    agent.run_store.write_task_state(task_state)
                    agent.emit_trace(task_state, "tool_executed", {
                        "name": name, "args": args, "tool_call_id": call.id, "result": clip(result, 500),
                        "duration_ms": int((time.monotonic() - tool_started_at) * 1000), **result_metadata})
                    checkpoint = agent.create_checkpoint(task_state, user_message, trigger="tool_executed")
                    agent.run_store.write_task_state(task_state)
                    agent.emit_trace(task_state, "checkpoint_created", {
                        "checkpoint_id": checkpoint["checkpoint_id"], "trigger": "tool_executed"})
                continue

            if kind == "retry":
                # retry 不是一次失败停机，而是把格式问题写回历史，
                # 让下一轮 prompt 明确提醒模型按工具/最终答案协议重答。
                agent.record({"role": "assistant", "content": payload, "created_at": now()})
                agent.run_store.write_task_state(task_state)
                continue

            final = payload.strip()
            return self._finish_success(task_state, user_message, final, run_started_at, raw)

        if tool_steps >= agent.max_steps:
            # 工具预算耗尽后还会再问模型一次，但这次只能总结，不能继续行动。
            # 这让 agent 有机会基于已有证据给出结果，而不是直接丢一个 step limit。
            task_state.record_attempt()
            agent.run_store.write_task_state(task_state)
            prompt_started_at = time.monotonic()
            prompt, prompt_metadata = agent._build_prompt_and_metadata(user_message)
            prompt += (
                "\n\nRuntime notice: the tool budget is exhausted. Do not call another tool. "
                "Use the evidence already present in the tool history and return exactly one "
                "non-empty plain text answer."
            )
            prompt_metadata["finalization"] = True
            agent.emit_trace(
                task_state,
                "prompt_built",
                {
                    "prompt_metadata": prompt_metadata,
                    "duration_ms": int((time.monotonic() - prompt_started_at) * 1000),
                    "purpose": "finalization",
                },
            )
            raw, kind, payload = self._request_model(
                task_state,
                user_message,
                prompt,
                prompt_metadata,
                run_started_at,
                purpose="finalization",
            )
            if kind == "final":
                final = payload.strip()
                return self._finish_success(task_state, user_message, final, run_started_at, raw)

        # 到这里说明模型既没有在预算内给出最终答案，也没有在最终化请求里按协议收尾。
        # 仍然写 checkpoint，是为了下一次运行可以从“已探索的信息”继续，而不是从零开始。
        if attempts >= max_attempts and tool_steps < agent.max_steps:
            final = "Stopped after too many malformed model responses without a valid tool call or final answer."
            task_state.stop_retry_limit(final)
        else:
            final = "Stopped after reaching the step limit without a final answer."
            task_state.stop_step_limit(final)
        agent.record({"role": "assistant", "content": final, "created_at": now()})
        agent.promote_durable_memory(user_message, final)
        agent.run_store.write_task_state(task_state)
        checkpoint = agent.create_checkpoint(task_state, user_message, trigger=task_state.stop_reason or "run_stopped")
        agent.emit_trace(
            task_state,
            "checkpoint_created",
            {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "trigger": task_state.stop_reason or "run_stopped",
            },
        )
        agent.emit_trace(
            task_state,
            "run_finished",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": final,
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
        return final
