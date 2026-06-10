"""交叉编码器(cross-encoder)重排序模块,按查询语言路由。

R8.F.4:在本项目的商品目录上,单一交叉编码器无法同时服务好中文和英文:
  * ``BAAI/bge-reranker-base``(中文训练,约 280 MB)在中文查询黄金集上
    跑出 recall@5 = 0.983 / MRR = 0.844 —— 中文基线上的最好成绩。
    但它不理解英文查询语义,"Give me an iPhone" 会把 iPad 重排到
    iPhone 之上(评测盲区 —— 当时黄金集全是中文)。
  * ``BAAI/bge-reranker-v2-m3``(多语言,约 568 MB)能正确处理英文
    ("Give me an iPhone" → iPhone 排第 1),但中文黄金集要损失 1.6
    个百分点,且 CPU 上延迟约为 3 倍。

解决方案:**按查询语言路由**。查询中只要包含至少一个中文字符 → 用 base
模型(中文句子里嵌英文品牌词的情况它也能正常处理);纯英文查询 → 用
多语言模型。这样中文指标保持在原始 0.983 基线不动,同时修复了英文查询
的失效模式。

环境变量覆盖(测试 / 回滚用):
  RERANK_MODEL_ZH=BAAI/bge-reranker-base
  RERANK_MODEL_MULTI=BAAI/bge-reranker-v2-m3
  RERANK_FORCE_MODEL=<name>   # 完全绕过路由逻辑
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Sequence


_CHINESE_MODEL = os.getenv("RERANK_MODEL_ZH", "BAAI/bge-reranker-base")
_MULTILINGUAL_MODEL = os.getenv("RERANK_MODEL_MULTI", "BAAI/bge-reranker-v2-m3")
_FORCE_MODEL = os.getenv("RERANK_FORCE_MODEL")  # 可选的强制覆盖
# 向后兼容:若设置了旧版 RERANK_MODEL 环境变量,则视为全局强制使用该模型。
_LEGACY_MODEL = os.getenv("RERANK_MODEL")
if _LEGACY_MODEL and not _FORCE_MODEL:
    _FORCE_MODEL = _LEGACY_MODEL


@lru_cache(maxsize=4)
def _model(name: str):
    from sentence_transformers import CrossEncoder
    # R10.perf —— max_length 是交叉编码器 CPU 开销的主导调节项
    # (注意力计算量约与序列长度成平方关系)。重排文档由标题+品牌+描述
    # 片段组成;256 token 能容纳完整片段,128 会截断长尾但相关性损失可
    # 忽略(信号主要来自标题/品牌/类目)。做成环境变量可调,便于在 CPU
    # VM 上用少量召回换延迟,且能即时回退。默认 256 = 原始行为。
    max_len = int(os.getenv("RERANK_MAX_LENGTH", "256"))
    return CrossEncoder(name, max_length=max_len)


def _has_ascii_letter(text: str) -> bool:
    """当且仅当 text 中至少包含一个 [A-Za-z] 字符时返回 True。
    用作交叉编码器选择器的路由依据。"""
    return any("a" <= c.lower() <= "z" for c in text or "")


def _pick_model_name(query: str) -> str:
    """返回本次查询应使用的交叉编码器模型名。

    启发式规则(R8.F.4 修订版):
      * 纯中文(完全没有 ASCII 字母)→ ``bge-reranker-base``
        —— 保住中文黄金集 recall@5 = 0.983 的基线。
      * 只要含有至少一个 ASCII 字母(纯英文、中文里嵌英文品牌词如
        "我想买 iPhone"、或纯英文产品型号)→ ``bge-reranker-v2-m3``,
        它能正确处理跨语言语义。

    为什么按"出现任意 ASCII 字母"而不是"ASCII 占多数"来路由:
      经验表明,只要出现英文品牌词 base 模型就会失效 —— 连
      "我想买 iPhone"(75% 是中文)都把 iPad 重排到了 iPhone 之上。
      多语言模型对两端都能处理,所以查询里只要出现英文就倾向用它,
      是更稳妥的选择。代价:仅这类查询延迟约 3 倍(约 270 ms vs
      约 95 ms;纯 CPU 环境,若在 GPU 上两者都约 100 ms)。
    """
    if _FORCE_MODEL:
        return _FORCE_MODEL
    if not query:
        return _CHINESE_MODEL
    return _MULTILINGUAL_MODEL if _has_ascii_letter(query) else _CHINESE_MODEL


def warmup_reranker() -> None:
    """在接流量之前把两个交叉编码器都加载好,并各跑一次极小的预测,
    让第一个真实请求不必承担模型加载开销。两个模型共享 lru_cache;
    顺序预热可把 CPU 上的冷启动控制在约 5-8 秒。
    """
    for name in {_CHINESE_MODEL, _MULTILINGUAL_MODEL}:
        try:
            model = _model(name)
            model.predict(
                [("推荐一款日常洁面产品", "温和洁面乳 适合日常清洁")],
                batch_size=1,
                show_progress_bar=False,
            )
        except Exception as e:  # noqa: BLE001
            import sys
            print(f"[rerank] warmup failed for {name}: {e}", file=sys.stderr)


def rerank(query: str, candidates: Sequence[dict], top_k: int = 5) -> list[dict]:
    """按交叉编码器对查询的打分对商品 dict 候选做重排序,返回前 top_k 个。
    出现任何失败(模型未安装等)时,回退为输入原始顺序。

    模型按查询逐条选择(见 `_pick_model_name`)。"""
    if not query or len(candidates) <= 1:
        return list(candidates)[:top_k]
    model_name = _pick_model_name(query)
    try:
        model = _model(model_name)
    except Exception as e:
        import sys
        print(f"[rerank] cross-encoder {model_name} unavailable: {e}", file=sys.stderr)
        return list(candidates)[:top_k]

    pairs: list[tuple[str, str]] = []
    for p in candidates:
        rag = p.get("rag_knowledge", {}) or {}
        doc = " ".join([
            p.get("title", ""),
            p.get("brand", ""),
            (rag.get("marketing_description") or "")[:240],
        ])
        pairs.append((query, doc))

    scores = model.predict(pairs, batch_size=8, show_progress_bar=False)
    ranked = sorted(zip(list(candidates), scores), key=lambda x: float(x[1]), reverse=True)
    # R9.A.2 —— 把重排分数挂到每个商品 dict 上,供 chat.py 在 iOS 的
    # "为什么推荐它" 调试卡片上展示。存放在私有键 "_retrieval" 中,
    # chat 路由读取后会先剥离该键,再发给 iOS 客户端。
    out: list[dict] = []
    for rank_idx, (p, s) in enumerate(ranked[:top_k]):
        sig = p.setdefault("_retrieval", {})
        sig["rerank_score"] = float(s)
        sig["rerank_rank"] = rank_idx
        sig["rerank_model"] = model_name
        out.append(p)
    return out
