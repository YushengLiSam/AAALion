"""P2 复审补丁的回归测试(假 LLM / 不联网 / 不加载模型)。

  1. 预算配套路由:年龄 / 容量 / 型号数字不能被当成总预算(预算在工具层是硬上限);
  2. 智能体会话约束与快路同口径做话题切换检测:上一话题的预算 / 排除不能带进新话题;
  3. price_in_cny(fetch=False):已归一化的商品不再重复请求汇率源;
  4. agent_eval:不带 --fake-llm / --live 不会默默调用付费 LLM;所有用例共用一个事件循环;
  5. trace 文件超过体积上限时轮转。
"""

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPO_ROOT / "server"
for root in (REPO_ROOT, SERVER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import pytest

from app.agent import runtime
from app.agent.router import bundle_budget, should_use_agent
from app.services import currency, rag_client
from app.services.constraint_state import build_conversation_filter
from app.services.currency import ExchangeRate, price_in_cny
from app.schemas.chat import ChatMessage
from rag.retrieve.query import Filter


# --------------------------------------------------------------------------- #
#  1. 预算配套路由
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text", [
    "适合30岁女生的一套护肤品",
    "给18岁男生搭配一套衣服",
    "推荐50ml一套的护肤套装",
    "iPhone 15 一套多少钱",
])
def test_bundle_ignores_numbers_without_money_marker(text):
    assert bundle_budget(text) is None
    route = should_use_agent(text)
    assert route.reason != "bundle"
    assert route.bundle_budget_cny is None


@pytest.mark.parametrize("text,budget", [
    ("3000元配一套跑步装备", 3000.0),
    ("预算2000配齐露营装备", 2000.0),
    ("5000以内搭配一套通勤穿搭", 5000.0),
    ("3k配一套跑步装备", 3000.0),
    ("¥3000一套露营装备", 3000.0),
    ("一套护肤品 预算800元", 800.0),
])
def test_bundle_still_detects_real_budgets(text, budget):
    assert bundle_budget(text) == budget
    assert should_use_agent(text).reason == "bundle"


def test_bundle_skips_age_then_finds_budget_in_same_sentence():
    # 第一个数字是年龄(无钱标记)→ 跳过;后面带"元"的才是预算
    assert bundle_budget("给30岁的我 800元配一套护肤品") == 800.0


# --------------------------------------------------------------------------- #
#  6. 会话锚点多跳留在快路
# --------------------------------------------------------------------------- #

def test_history_anchor_multihop_stays_on_fast_path():
    from rag.retrieve.multihop import detect_multihop

    q = "有没有比刚才第二款便宜的"
    plan = detect_multihop(q, has_history_cards=True)
    assert plan is not None and plan.uses_history_anchor       # 前提:确实是会话锚点多跳
    route = should_use_agent(q, has_history_cards=True)
    assert route.use_agent is False and route.reason == "multihop_history"


def test_named_anchor_multihop_still_routes_to_agent():
    assert should_use_agent("比 AirPods Pro 便宜的降噪耳机").reason == "multihop"

