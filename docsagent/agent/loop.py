from __future__ import annotations

import json
from typing import Callable, Protocol


class ToolCallingModel(Protocol):
    request_format: str

    def chat(self, messages: list[dict[str, str]], temperature: float | None = None) -> str: ...

    def chat_turn(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None = None,
    ) -> tuple[str, list[dict]]: ...

    def assistant_tool_message(self, content: str, calls: list[dict]) -> dict: ...

    def tool_result_message(self, call: dict, result: str, fmt: str) -> dict: ...


class AgentLoop:
    """Run bounded model/tool turns and return a result grounded in tool evidence."""

    def run(
        self,
        model: ToolCallingModel,
        messages: list[dict],
        tools: list[dict] | None = None,
        execute: Callable[[str, dict], dict] | None = None,
        max_rounds: int = 4,
        temperature: float | None = None,
        max_tool_calls: int = 12,
        max_tool_output_chars: int = 30000,
        max_context_chars: int = 128000,
        on_event: Callable[..., None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> str:
        def emit(event: str, **payload) -> None:
            if on_event is None:
                return
            on_event(event, **payload)

        history = list(messages)
        tools = tools or []
        context_chars = len(json.dumps(tools, ensure_ascii=False)) + sum(len(json.dumps(item, ensure_ascii=False)) for item in history)
        if context_chars > max_context_chars:
            raise RuntimeError(f"agent reached the {max_context_chars} character context budget")

        def synthesize_from_history(reason: str) -> str:
            emit("status", phase="finalizing", message=f"{reason}，正在基于已有证据整理回答")
            final_history = self._flatten_tool_history(history, reason)
            try:
                return model.chat(final_history, temperature=temperature)
            except Exception as exc:
                emit("status", phase="error", message=f"已有证据总结失败：{exc}")
                raise RuntimeError(f"agent could not synthesize collected evidence: {exc}") from exc

        if not tools or execute is None:
            emit("status", phase="model", message="正在等待模型回答")
            return model.chat(history)

        tool_calls = 0
        output_chars = 0
        seen_calls: set[str] = set()
        for round_index in range(max_rounds):
            if cancel_check:
                cancel_check()
            status = {
                "round": round_index + 1,
                "max_rounds": max_rounds,
                "tool_calls": tool_calls,
                "max_tool_calls": max_tool_calls,
                "tool_outputs_chars": output_chars,
                "max_tool_outputs_chars": max_tool_output_chars,
            }
            emit("round", round=status["round"], max_rounds=max_rounds, tool_calls=tool_calls, max_tool_calls=max_tool_calls)
            status_message = {"role": "user", "content": f"<agent_status>{json.dumps(status, ensure_ascii=False)}</agent_status>"}
            request_history = [*history, status_message]
            context_chars = len(json.dumps(tools, ensure_ascii=False)) + sum(len(json.dumps(item, ensure_ascii=False)) for item in request_history)
            if context_chars > max_context_chars:
                raise RuntimeError(f"agent reached the {max_context_chars} character context budget")
            emit("status", phase="model", message=f"正在等待模型响应（第 {status['round']}/{max_rounds} 轮）")
            content, calls = model.chat_turn(request_history, tools, temperature)
            if not calls:
                emit("status", phase="finalizing", message="模型已完成决策，正在整理回答")
                return content
            if len(calls) > 8:
                return synthesize_from_history("模型本轮请求超过 8 个工具调用，本轮工具调用因超出限制而未执行")

            fingerprints = set()
            for call in calls:
                try:
                    normalized = self._normalize_tool_args(call.get("arguments"))
                except ValueError:
                    normalized = {"invalid_arguments": str(call.get("arguments", ""))[:500]}
                signature = json.dumps([call.get("name"), normalized], sort_keys=True, ensure_ascii=False)
                call["blocked_duplicate"] = signature in fingerprints or signature in seen_calls
                fingerprints.add(signature)
                seen_calls.add(signature)
            if tool_calls + len(calls) > max_tool_calls:
                return synthesize_from_history(f"已达到工具调用上限（{max_tool_calls} 次），本轮工具调用因超出限制而未执行")

            tool_calls += len(calls)
            history.append(model.assistant_tool_message(content, calls))
            for call_index, call in enumerate(calls, 1):
                if cancel_check:
                    cancel_check()
                call_id = str(call.get("id") or f"tool-{tool_calls - len(calls) + call_index}")
                tool_name = str(call.get("name") or "unknown")
                emit("tool_started", call_id=call_id, tool=tool_name, summary=self._tool_call_summary(call))
                if call.get("blocked_duplicate"):
                    result = {"ok": False, "error": "identical tool name and arguments were already executed; use the previous result or choose different arguments"}
                else:
                    try:
                        arguments = self._normalize_tool_args(call.get("arguments"))
                        result = {"ok": True, **execute(call["name"], arguments)}
                    except Exception as exc:
                        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                if cancel_check:
                    cancel_check()
                if result.get("ok") and result.get("diff"):
                    emit(
                        "artifact",
                        kind="file_diff",
                        path=result.get("path", ""),
                        diff=result["diff"],
                        sha256=result.get("sha256", ""),
                    )
                emit("tool_finished", call_id=call_id, tool=tool_name, ok=bool(result.get("ok")), summary=self._tool_result_summary(result))
                serialized = json.dumps(result, ensure_ascii=False)
                if output_chars + len(serialized) > max_tool_output_chars:
                    serialized = json.dumps({"ok": False, "error": "per-turn project tool output budget reached; narrow the query or read a smaller line range"}, ensure_ascii=False)
                    if output_chars + len(serialized) > max_tool_output_chars:
                        raise RuntimeError("agent reached the per-turn project tool output budget")
                output_chars += len(serialized)
                history.append(model.tool_result_message(call, serialized, model.request_format))
        return synthesize_from_history(f"已达到执行轮次上限（{max_rounds} 轮）")

    @staticmethod
    def _normalize_tool_args(arguments) -> dict:
        if isinstance(arguments, dict):
            return arguments
        if arguments is None or arguments == "":
            return {}
        try:
            parsed = json.loads(arguments)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("tool arguments were not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments must be a JSON object")
        return parsed

    @staticmethod
    def _flatten_tool_history(history: list[dict], reason: str) -> list[dict[str, str]]:
        flattened = []
        tool_names = {}

        def append(role: str, content: str) -> None:
            if role != "system" and flattened and flattened[-1]["role"] == role:
                flattened[-1]["content"] += f"\n\n{content}"
            else:
                flattened.append({"role": role, "content": content})

        for item in history:
            role = item.get("role")
            if role in {"system", "user"}:
                append(role, str(item.get("content", "")))
                continue
            if role == "assistant":
                content = str(item.get("content") or "")
                calls = item.get("tool_calls") or []
                if calls:
                    names = []
                    for call in calls:
                        name = str(call.get("function", {}).get("name") or "unknown")
                        tool_names[call.get("id")] = name
                        names.append(name)
                    content = f"{content}\n已请求工具：{', '.join(names)}".strip()
                if content:
                    append("assistant", content)
                continue
            if role == "tool":
                call_id = item.get("tool_call_id") or item.get("provider_call_id")
                name = str(item.get("name") or tool_names.get(call_id) or "工具")
                append("user", f"工具 {name} 返回了以下结果：\n{item.get('content', '')}")
        append("user", f"{reason}。请只基于以上已经获得的证据直接回答最初请求，不要再调用工具；明确区分已确认事实、推断和仍缺少的信息。")
        return flattened

    @staticmethod
    def _tool_call_summary(call: dict) -> str:
        arguments = call.get("arguments")
        try:
            arguments = AgentLoop._normalize_tool_args(arguments)
        except ValueError:
            raw = "" if arguments is None else str(arguments)
            return f"参数格式无效：{raw[:200]}"
        values = []
        for key in ("path", "query", "kind", "target_directory", "offset", "limit"):
            value = arguments.get(key)
            if value not in (None, ""):
                values.append(f"{key}={value}")
        files = arguments.get("files")
        if isinstance(files, list) and files:
            values.append(f"files={len(files)}")
        return " · ".join(values)[:240]

    @staticmethod
    def _tool_result_summary(result: dict) -> str:
        if not result.get("ok"):
            return str(result.get("error") or "执行失败")[:280]
        if "items" in result:
            return f"返回 {result.get('shown', len(result.get('items', [])))} 项"
        if result.get("proposal_ready"):
            return "草稿已提交审阅"
        if result.get("written") is False:
            return "未写入磁盘"
        return "执行完成"
