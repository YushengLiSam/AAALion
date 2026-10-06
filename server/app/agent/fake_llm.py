"""脚本化的假 LLM(只实现 chat_tools),给单测和 `agent_eval.py --fake-llm` 用。

不联网、不花钱、结果确定:按脚本逐轮返回工具调用;脚本里的
{"submit": "auto"} 会从已回填的 role=tool 结果里按出现顺序挑商品 ID 提交
(参照商品 anchor 排第一),模拟一个"照规矩办事"的模型。用它跑通的是
**编排 + 工具 + 约束 + 回填**这一整条链路,不代表真实模型的决策质量——
真实效果只能用 live 模式(真 key)评测。
"""

from __future__ import annotations

import asyncio
import json


def _first_per_tool(messages: list[dict]) -> list[str]:
    """每条工具结果各取第一个商品(预算配套:每个品类挑一件)。"""
    out: list[str] = []
    for m in messages:
        if m.get("role") != "tool":
            continue
        try:
            payload = json.loads(m.get("content") or "{}")
        except ValueError:
            continue
        rows = [r for r in payload.get("results") or [] if isinstance(r, dict) and r.get("id")]
        if rows and rows[0]["id"] not in out:
            out.append(rows[0]["id"])
    return out


def _ids_from_tool_messages(messages: list[dict]) -> list[str]:
    anchors: list[str] = []
    ids: list[str] = []
    for m in messages:
        if m.get("role") != "tool":
            continue
        try:
            payload = json.loads(m.get("content") or "{}")
        except ValueError:
            continue
        a = (payload.get("anchor") or {}).get("id") if isinstance(payload.get("anchor"), dict) else None
        if a:
            anchors.append(a)
        for r in payload.get("results") or payload.get("rows") or []:
            if isinstance(r, dict) and r.get("id"):
                ids.append(r["id"])
        if payload.get("citable") and payload.get("id"):
            ids.append(payload["id"])
    return list(dict.fromkeys([*anchors, *ids]))


class ScriptedToolLLM:
    """script: 每一轮是一个 list,元素为 {"name", "arguments"} 或 {"submit": "auto"|[ids], "n": 4}。

    `delay_s` 用来模拟慢模型(测超时回退);`fail_at` 让第 N 次调用抛异常。
    `content` 会作为前导文本一起返回(测"前导文本只进 trace")。"""

    name = "fake"
    supports_tools = True

    def __init__(self, script: list[list[dict]], *, delay_s: float = 0.0,
                 fail_at: int | None = None, content: str | None = None) -> None:
        self.script = [list(step) for step in script]
        self.delay_s = delay_s
        self.fail_at = fail_at
        self.content = content
        self.calls: list[dict] = []

    async def stream_chat(self, messages):  # 快路不会用到;保持接口完整
        yield "[fake]"

    async def chat_tools(self, messages, tools, tool_choice="auto", **kwargs) -> dict:
        idx = len(self.calls)
        self.calls.append({"n_messages": len(messages),
                           "tools": [t["function"]["name"] for t in tools or []], **kwargs})
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail_at is not None and idx == self.fail_at:
            raise RuntimeError("scripted upstream failure")
        allowed = {t["function"]["name"] for t in tools or []}
        step = self.script[idx] if idx < len(self.script) else [{"submit": "auto"}]
        tool_calls = []
        for j, item in enumerate(step):
            if "submit" in item:
                ids = item["submit"]
                if ids == "auto":
                    ids = _ids_from_tool_messages(messages)[: int(item.get("n", 4))]
                elif ids == "first_per_tool":
                    ids = _first_per_tool(messages)[: int(item.get("n", 6))]
                tool_calls.append({"id": f"toolu_fake_{idx}_{j}", "name": "submit_products",
                                   "arguments": {"product_ids": list(ids), "note": "scripted"}})
            elif item.get("name") in allowed:
                tool_calls.append({"id": f"toolu_fake_{idx}_{j}", "name": item["name"],
                                   "arguments": dict(item.get("arguments") or {})})
        if not tool_calls and "submit_products" in allowed:
            tool_calls.append({"id": f"toolu_fake_{idx}_s", "name": "submit_products",
                               "arguments": {"product_ids": _ids_from_tool_messages(messages)[:4]}})
        chars = sum(len(str(m.get("content") or "")) for m in messages)
        return {
            "content": self.content,
            "tool_calls": tool_calls,
            "usage": {"prompt_tokens": chars // 2, "completion_tokens": 20 * len(tool_calls),
                      "total_tokens": chars // 2 + 20 * len(tool_calls)},
            "finish_reason": "tool_calls" if tool_calls else "stop",
            "model": "fake-scripted",
        }
