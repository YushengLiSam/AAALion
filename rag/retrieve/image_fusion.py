"""拍照找货 + 文字:CLIP 视觉召回与用户文字约束的融合(全程不调用任何 LLM)。

旧行为(IMAGE_TEXT_FUSION=0,或本模块任何异常时 chat.py 回退到这条):
只把第一张图送 CLIP 取 top-3,用户配的文字完全不参与检索,也没有相关性下限——
一张和商品毫不相干的照片照样出 3 张卡;"这个有没有便宜点的"返回的还是原价那件。

新流程(本模块只做"给定视觉候选 → 选卡"这一段纯计算,CLIP / 向量库 / 交叉编码器
都由调用方注入或懒加载,单测可以全部打桩):

  1. 召回:每张图(最多 IMAGE_MAX_QUERY_IMAGES 张)各取 CLIP top-IMAGE_RECALL_N,
     按商品取最大相似度合并(rag.retrieve.query.query_images)。
  2. 视觉相关性下限 IMAGE_MIN_SIM:低于下限的候选一律不出卡;全部低于下限 →
     status="below_floor",由调用方决定走文字检索还是如实说"没有相似商品"。
     过下限的候选再钉在锚点(视觉最相似商品)的大类 / 细分品类上(近似并列除外)。
  3. 硬约束:复用文字路径同一套解析(build_retrieval_filter / build_conversation_filter
     的 Filter + apply_product_filter 严格人民币口径),再叠加**本地**否定规则
     (国别词、否定语境里的品牌、"X以外"、"国产"、否定语境里的属性词)。
     注意 negation.extract_negation 在配置了 key 时会调 LLM,这里一律不用它。
     "便宜点 / 贵一点"这类**相对**价格以视觉最相似商品(锚点)的人民币价为基准。
  4. 文字排序:文字里有超出"这个 / 同款 / 多少钱 / 有没有"之外的描述时,用本地
     交叉编码器给幸存候选打文字相关分并融合(视觉为主,公式见 fuse_scores);
     否则保持纯视觉顺序。
  5. 约束把视觉候选全部筛掉 → status="constraints_emptied",给出回退用的 Filter
     (锚点的品类 / 细分品类 + 同一组约束),由调用方走文字检索
     ("这个有没有便宜点的" → 同类里更便宜的)。

所有阈值都是环境变量,默认值见下方常量;DEFAULT_MIN_SIM 的取值依据见
docs/IMAGE_SEARCH.md(合成负样本 vs 增强过的目录图,**没有**用真实用户照片校准)。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from rag.retrieve.query import Filter, apply_product_filter

# ---------------------------------------------------------------------------
# 开关与阈值
# ---------------------------------------------------------------------------

# 视觉相关性下限(CLIP ViT-B/32 余弦相似度)。取值依据:rag/eval/image_text_eval.py
# --calibrate 实测(docs/bench/image_text_eval_local.json):
#   * 正样本:145 张目录图各做 3 份确定性增强(裁剪/旋转/调色/缩小/JPEG),
#     共 435 张,top-1 相似度最低 0.508、1% 分位 0.592、中位数 0.805;
#   * 负样本:50 张合成无关图(噪声/纯色/渐变/文字/棋盘条纹/几何图形),
#     top-1 相似度最高 0.4835(一张渐变图)、中位数 0.437。
# 取"负样本最大值 + 0.02 余量"向上取整 = 0.51:合成负样本全部拒掉,增强正样本
# 保留 99.8%(434/435)。两个分布之间只隔 0.02 左右,真实用户照片会比增强图
# 分数更低,所以宁可取低不取高——这是刻意保守的默认值,只保证"纯噪声/纯色/
# 截图文字"这类图不出随机卡,**未在真实的目录外用户照片上校准**。
DEFAULT_MIN_SIM = 0.51
# 文字相关分的融合权重 λ:final = clip_sim + λ · ce_score(ce_score ∈ [0,1])。
# 0.10 意味着文字最多只能在 CLIP 相似度相差 0.10 以内的候选之间改变先后。
DEFAULT_TEXT_WEIGHT = 0.10
# 品类钉住的近似并列余量:视觉候选默认只保留与锚点(视觉最相似商品)同大类或
# 同细分品类的商品;和锚点相似度相差不到这个余量的跨品类候选也保留
# (CLIP 自己都分不清是哪一类时,不替它做决定)。
DEFAULT_CROSS_CAT_MARGIN = 0.03


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, "") or default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name, "")
        return float(raw) if raw.strip() else default
    except ValueError:
        return default


def fusion_enabled() -> bool:
    """IMAGE_TEXT_FUSION=1(默认开);0 → chat.py 走旧的纯 CLIP top-3。"""
    return os.getenv("IMAGE_TEXT_FUSION", "1") == "1"


def recall_n() -> int:
    return _env_int("IMAGE_RECALL_N", 20)


def max_query_images() -> int:
    return _env_int("IMAGE_MAX_QUERY_IMAGES", 3)


def image_top_k() -> int:
    return _env_int("IMAGE_TOP_K", 3)


def min_sim() -> float:
    return _env_float("IMAGE_MIN_SIM", DEFAULT_MIN_SIM)


def text_weight() -> float:
    return _env_float("IMAGE_TEXT_WEIGHT", DEFAULT_TEXT_WEIGHT)


def cross_cat_margin() -> float:
    """IMAGE_CROSS_CAT_MARGIN;设成负数 = 关闭品类钉住(保留所有过下限的候选)。"""
    return _env_float("IMAGE_CROSS_CAT_MARGIN", DEFAULT_CROSS_CAT_MARGIN)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class VisualCandidate:
    product_id: str
    sim: float
    product: dict


@dataclass
class LocalNegation:
    exclude_brands: list[str] = field(default_factory=list)
    country_keywords: list[str] = field(default_factory=list)
    title_keywords: list[str] = field(default_factory=list)
    except_brands: list[str] = field(default_factory=list)
    requires_domestic: bool = False

    @property
    def active(self) -> bool:
        return bool(self.exclude_brands or self.country_keywords or self.title_keywords
                    or self.except_brands or self.requires_domestic)


@dataclass
class ImageFusionResult:
    # visual            —— 有幸存的视觉候选,products 就是要出的卡
    # below_floor       —— 视觉最高分低于 IMAGE_MIN_SIM,不出视觉卡
    # constraints_emptied —— 有视觉相似的商品,但全部不满足文字约束;
    #                      调用方用 fallback_filter / fallback_query 走文字检索
    # no_visual         —— CLIP 没返回任何候选(模型/索引不可用),调用方走旧兜底
    status: str
    products: list[dict] = field(default_factory=list)
    enforced: list[str] = field(default_factory=list)
    anchor: dict | None = None
    anchor_excluded: bool = False
    reordered_by_text: bool = False
    top_sim: float | None = None
    floor: float | None = None
    negation: LocalNegation | None = None
    fallback_filter: Filter | None = None
    fallback_query: str | None = None
    fallback_intent: str | None = None
    history_dropped: bool = False
    trace: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 候选合并
# ---------------------------------------------------------------------------


def merge_visual_hits(hit_lists: Iterable[Iterable]) -> list[VisualCandidate]:
    """多张图的命中按商品取最大相似度合并,降序返回。

    接受任何带 ``product_id`` / ``score`` / ``product`` 属性的对象
    (rag.retrieve.query.Hit),也接受 (product_id, score, product) 三元组。
    """
    best: dict[str, VisualCandidate] = {}
    for hits in hit_lists:
        for h in hits or []:
            if isinstance(h, tuple):
                pid, score, prod = h
            else:
                pid, score, prod = h.product_id, h.score, h.product
            if not pid or not isinstance(prod, dict):
                continue
            score = float(score)
            prev = best.get(pid)
            if prev is None or score > prev.sim:
                best[pid] = VisualCandidate(pid, score, prod)
    return sorted(best.values(), key=lambda c: c.sim, reverse=True)


# ---------------------------------------------------------------------------
# 文字解析(全部本地规则)
# ---------------------------------------------------------------------------

# "是不是苹果的" / "要不要买华为" 是**提问**,不是排除。负向规则(_NEG_PHRASE_RE /
# constraints._NEGATED_PREFIX_RE)按子串匹配会把 "是不是苹果" 里的 "不是苹果"、
# "要不要买华为" 里的 "不要买华为" 当成排除 —— 对配图提问("这是不是华为的")
# 尤其常见,会把图里那个品牌整个筛掉。先把 X不X 句式折叠成 X。
_XNOTX_RE = re.compile(r"([是要能会可好用])不\1")


def normalize_question_forms(text: str) -> str:
    if not text:
        return ""
    text = _XNOTX_RE.sub(r"\1", text)
    return text.replace("有没有", "有")


# "这是不是苹果的 / 这是华为的吗 / 什么牌子" 是在**辨认**图里的东西,品牌是被问的对象,
# 不是"只要这个品牌"。文字路径会把它解析成 brand_include,视觉路径照搬的话,
# 图里明明是华为、问一句"是苹果的吗"就会把华为整个筛掉。要在规整问句之前判断。
_IDENT_RE = re.compile(
    r"是不是|是否|是[^，。,;；!！]{1,16}?(?:吗|嘛|么)|(?:什么|哪个|哪家|啥)(?:牌子|品牌)|\bis (?:this|it)\b",
    re.IGNORECASE,
)


def is_identification_question(text: str) -> bool:
    return bool(text and _IDENT_RE.search(text))


_CLAUSE_SPLIT_RE = re.compile(r"[，。,;；!！?？\n]+")


def identification_brands(text: str) -> list[str]:
    """辨认式提问**所在分句**里点名的品牌(canonical 名,与 build_retrieval_filter 同口径)。
    只有这些品牌不当 brand_include:"这是什么?我只要华为的" 里 "华为" 是真要求,
    不能因为同一句话里有个 "这是什么" 就被一起丢掉。"""
    if not text:
        return []
    try:
        from rag.retrieve.constraints import build_retrieval_filter
    except Exception:
        return []
    out: list[str] = []
    for clause in _CLAUSE_SPLIT_RE.split(text):
        if not clause.strip() or not is_identification_question(clause):
            continue
        f = build_retrieval_filter(normalize_question_forms(clause))
        for b in (f.brand_include if f else None) or []:
            if b not in out:
                out.append(b)
    return out


_NEG_TRIGGERS = (
    "不要", "别要", "别给我", "不想要", "不需要", "不考虑", "不买", "不选",
    "不含", "不带", "除了", "排除", "就算了", "就不看", "不用了",
)

# 否定语境里的属性词("不要粉色的" / "不含酒精")。只匹配**标题**(比文字路径的
# 标题+营销描述更保守:描述里常写"不含酒精",按描述匹配会把恰好符合要求的商品筛掉)。
_KW_NEG_RE = re.compile(
    r"(?:不想要|不需要|不要|别要|别给我|不含|不带|排除|除了)\s*([^,，。;；！!?？、\s]{1,12})"
)
_KW_STRIP_PREFIX_RE = re.compile(r"^(?:含有|含|带有|带|有|要|是|这种|那种|这个|那个|这款|那款|太|那么|这么|用)+")
_KW_STRIP_SUFFIX_RE = re.compile(r"(?:的|款|类|系列|了|啦|呢|吧|哦|啊|呀)+$")
_KW_STOP = frozenset({
    "这个", "那个", "这款", "那款", "一样", "同款", "它", "这些", "那些", "别的", "其他",
})
_KW_PRICEISH_RE = re.compile(r"[\d贵价钱元块]|便宜|预算|超过|以上|以内")


def _local_keyword_exclusions(text: str, skip: Sequence[str]) -> list[str]:
    skip_l = [s.lower() for s in skip if s]
    out: list[str] = []
    for m in _KW_NEG_RE.finditer(text or ""):
        kw = _KW_STRIP_PREFIX_RE.sub("", m.group(1))
        kw = _KW_STRIP_SUFFIX_RE.sub("", kw).strip()
        if len(kw) < 2 or len(kw) > 8 or kw in _KW_STOP or _KW_PRICEISH_RE.search(kw):
            continue
        low = kw.lower()
        # 品牌 / 国别词另有专门规则(别名、产地解析),不按标题子串重复处理
        if any(low == s or low in s or s in low for s in skip_l):
            continue
        if kw not in out:
            out.append(kw)
    return out


def local_negation(text: str) -> LocalNegation:
    """本地否定抽取 —— extract_negation 的"无 LLM"版本,外加 国产 / X以外。"""
    neg = LocalNegation()
    if not text:
        return neg
    try:
        from rag.retrieve.negation import (
            _local_brand_mentions,
            _local_country_keywords,
            except_brands,
            requires_domestic,
        )
    except Exception:
        return neg
    if any(t in text for t in _NEG_TRIGGERS):
        neg.exclude_brands = _local_brand_mentions(text)
        neg.country_keywords = _local_country_keywords(text)
        neg.title_keywords = _local_keyword_exclusions(
            text, skip=[*neg.exclude_brands, *neg.country_keywords, *_country_words()])
    neg.except_brands = except_brands(text)
    neg.requires_domestic = requires_domestic(text)
    return neg


def _country_words() -> list[str]:
    try:
        from rag.retrieve.brand_origin import COUNTRY_KEYWORDS
        return [kw for kws in COUNTRY_KEYWORDS.values() for kw in kws]
    except Exception:
        return []


# 相对价格:以视觉最相似商品为基准。"太贵" 是嫌贵 → 要更便宜的;"太便宜" 对称。
# 注意 "好一点 / 质量好点" 说的是品质不是价格,不能翻译成"价格高于锚点"的硬约束
# ("降噪效果好一点的" 曾被误判成 pricier,把同价位/更便宜的好耳机全筛掉)。
_CHEAPER_RE = re.compile(
    r"便宜(?:一?点|一些|些|的)|更便宜|再便宜|太贵|嫌贵|平价一?点|低价一?点"
    r"|\bcheaper\b|\bless expensive\b|\btoo expensive\b|\blower price\b",
    re.IGNORECASE,
)
_PRICIER_RE = re.compile(
    r"贵(?:一?点|一些|些)|更贵|更高端|高端一?点|太便宜|嫌便宜"
    r"|\bpricier\b|\bmore expensive\b|\bhigher[- ]end\b|\btoo cheap\b",
    re.IGNORECASE,
)
# 紧挨在前面的否定("不要便宜的 / 别买便宜货 / 不是要更贵的")把方向取消掉。
# "不要太贵 / 不要太便宜" 不在此列:"太X" 本身就是嫌 X,前面加"不要"意思不变。
_REL_NEG_BEFORE_RE = re.compile(r"(?:不要|别要|不想要|不需要|别买|不买|不是|别)[^，。,;；!！?？]{0,2}$")


def _rel_hit(regex: re.Pattern, text: str) -> bool:
    for m in regex.finditer(text):
        if m.group(0).startswith("太") or not _REL_NEG_BEFORE_RE.search(text[: m.start()]):
            return True
    return False


def relative_price_direction(text: str) -> str | None:
    """'cheaper' / 'pricier' / None。两个方向同时出现时不猜,返回 None。"""
    if not text:
        return None
    cheap = _rel_hit(_CHEAPER_RE, text)
    pricey = _rel_hit(_PRICIER_RE, text)
    if "太贵" in text and "太便宜" not in text:
        return "cheaper"
    if "太便宜" in text and "太贵" not in text:
        return "pricier"
    if cheap and not pricey:
        return "cheaper"
    if pricey and not cheap:
        return "pricier"
    return None


# "这个 / 同款 / 这是什么 / 多少钱 / 有没有" 这类指代、询价、有无的话不带描述信息;
# 价格短语、否定从句、产地词已经作为硬约束执行,也不算描述。剥掉这些之后还剩
# 实义内容(≥2 个汉字或一个 ≥3 字母的英文词),才说明用户在描述想要的样子。
_NEG_CLAUSE_RE = re.compile(
    r"(?:不想要|不需要|也不要|不要|别要|别给我|不考虑|不买|不选|不含|不带|除了|排除|避开)"
    r"[^，。；,;！!？?]*"
)
_EXCEPT_CLAUSE_RE = re.compile(r"[^，。；,;！!？?\s]{1,16}(?:以外|之外)")
_PRICE_PHRASE_RE = re.compile(
    r"(?:预算|价位|价格|不要?超过|最多|顶多|under|below|less than|within|up to|over|above)?\s*"
    r"[¥￥$]?\s*\d+(?:\.\d+)?\s*(?:元|块|rmb|RMB|w|万|k)?\s*"
    r"(?:以内|以下|以上|之内|内|封顶|左右|上下|起步|起)?",
    re.IGNORECASE,
)
_PRICE_WORD_RE = re.compile(
    r"便宜|太贵|更贵|贵|平价|低价|高端|划算|性价比|实惠|cheaper|cheap|pricier|expensive|budget",
    re.IGNORECASE,
)
_DOMESTIC_WORD_RE = re.compile(r"国产|国货|国内品牌|本土品牌|民族品牌")
_DEICTIC_RE = re.compile(
    r"这是什么|是什么|什么(?:东西|牌子|品牌|型号|价位?)|哪(?:个|家)?(?:牌子|品牌)|多少钱|什么价|价钱|多钱"
    r"|有没有|有吗|有卖吗?|卖吗|能买吗|在哪(?:里)?买|哪里买|怎么样|好不好|好用吗|值得买吗?|值不值"
    r"|[这那](?:个|款|件|双|种|台|部|只|瓶|本|套|支|条|顶|盒|样|些)"
    r"|同款|一模一样|一样|类似|相似|差不多|相同|它"
    r"|图片?(?:里|中|上)?|照片(?:里|中|上)?|拍的|截图"
    r"|帮我|帮忙|麻烦|请问|请|找一?下|找|搜一?下|搜|看一?下|看看|识别一?下|识别|推荐一?下|推荐"
    r"|我想要|我想买|我要|想要|想买|要买|买|还有|别的|其他|更多|款式?|商品|东西|货"
    r"|一下|一点|一些|吗|呢|啊|吧|呀|哦|嘛|么|的|了|个|点|些|有|是|我|你|们|和|跟|与|也|都|就|再|还|要"
)
_EN_STOP = frozenset({
    "this", "that", "these", "those", "it", "its", "is", "are", "was", "what", "whats",
    "how", "much", "price", "cost", "do", "does", "you", "have", "has", "any", "find",
    "show", "me", "similar", "same", "like", "one", "ones", "the", "a", "an", "item",
    "product", "please", "can", "could", "i", "buy", "get", "where", "in", "photo",
    "picture", "image", "pic", "something", "else", "other", "more", "there", "here",
    "to", "of", "for", "with", "and", "or", "want", "need", "looking", "look", "see",
    "sell", "available", "brand", "which", "who", "made", "makes", "exactly", "stuff",
})


def strip_constraint_phrases(text: str) -> str:
    """去掉否定从句 / X以外 / 价格短语 / 相对价格词 / 产地词 —— 剩下的才交给交叉编码器。
    (否定从句不能进交叉编码器:"不要苹果" 会把苹果的商品打高分。)"""
    s = _NEG_CLAUSE_RE.sub(" ", text or "")
    s = _EXCEPT_CLAUSE_RE.sub(" ", s)
    s = _PRICE_PHRASE_RE.sub(" ", s)
    s = _PRICE_WORD_RE.sub(" ", s)
    s = _DOMESTIC_WORD_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip(" ，,。.!！?？;；")


def descriptive_residual(text: str) -> str:
    """剥掉约束短语与指代/询价套话后剩下的描述性内容(可能为空串)。"""
    s = strip_constraint_phrases(normalize_question_forms(text))
    s = _DEICTIC_RE.sub(" ", s)
    words = [w for w in re.findall(r"[A-Za-z]+", s) if w.lower() not in _EN_STOP and len(w) >= 3]
    cjk = "".join(re.findall(r"[一-鿿]+", s))
    return " ".join(([cjk] if cjk else []) + words).strip()


def has_descriptive_intent(text: str) -> bool:
    res = descriptive_residual(text)
    cjk = len(re.findall(r"[一-鿿]", res))
    return cjk >= 2 or any(len(w) >= 3 for w in re.findall(r"[A-Za-z]+", res))


# ---------------------------------------------------------------------------
# 约束
# ---------------------------------------------------------------------------


def _subs(f: Filter | None) -> list[str]:
    if f is None:
        return []
    subs = list(f.sub_categories or [])
    if f.sub_category and f.sub_category not in subs:
        subs.append(f.sub_category)
    return subs


def _alias_terms(names: Iterable[str]) -> set[str]:
    terms: set[str] = set()
    try:
        from rag.retrieve.brand_origin import expand_brand_aliases
    except Exception:
        expand_brand_aliases = None  # type: ignore
    for n in names:
        if not n:
            continue
        terms.add(str(n).casefold())
        if expand_brand_aliases is not None:
            try:
                tokens = [n] + str(n).replace("（", " ").replace("）", " ").split()
                for tok in tokens:
                    terms |= {str(t).casefold() for t in expand_brand_aliases(tok)}
            except Exception:
                pass
    return {t for t in terms if t}


def _brand_hit(product: dict, terms: set[str]) -> bool:
    b = str(product.get("brand") or "").casefold()
    return bool(b) and any(t in b or (len(b) >= 2 and b in t) for t in terms)


def effective_filter(
    turn_filter: Filter | None,
    conversation_filter: Filter | None,
    anchor: dict | None,
    *,
    drop_turn_brands: bool = False,
    asked_brands: Iterable[str] | None = None,
) -> tuple[Filter | None, bool]:
    """视觉路径用的硬约束:默认沿用会话约束(与文字路径一致,预算/排除跨轮生效);
    但**照片本身就是本轮的话题锚点**——会话里继承的品类 / 细分品类与图中最相似
    商品对不上、且本轮文字没有重新指定品类时,视为换话题:丢掉继承的品类、细分
    品类、预算和点名品牌,只保留跨轮排除(品牌 / 国别)。返回 (filter, 是否丢了历史)。

    asked_brands:辨认式提问("是不是苹果的")里被问到的品牌(identification_brands),
    不作为 brand_include。drop_turn_brands=True 是更粗的旧口径:本轮点名的品牌全部丢掉。
    """
    flt, dropped = _effective_filter(turn_filter, conversation_filter, anchor)
    asked = {b.casefold() for b in (asked_brands or [])}
    if drop_turn_brands and turn_filter is not None and turn_filter.brand_include:
        asked |= {b.casefold() for b in turn_filter.brand_include}
    if asked and flt is not None and flt.brand_include:
        kept = [b for b in (flt.brand_include or []) if b.casefold() not in asked]
        flt = Filter(**{k: getattr(flt, k) for k in flt.__dataclass_fields__})
        flt.brand_include = kept or None
        if not flt.active:
            flt = None
    return flt, dropped


def _effective_filter(
    turn_filter: Filter | None,
    conversation_filter: Filter | None,
    anchor: dict | None,
) -> tuple[Filter | None, bool]:
    conv = conversation_filter
    if conv is None:
        return turn_filter, False
    if anchor is None:
        return conv, False
    a_cat = anchor.get("category")
    a_sub = anchor.get("sub_category")
    t_cat = turn_filter.category if turn_filter else None
    t_subs = _subs(turn_filter)
    c_subs = _subs(conv)
    switched = bool(conv.category and a_cat and conv.category != a_cat and not t_cat)
    switched = switched or bool(c_subs and a_sub and a_sub not in c_subs and not t_subs)
    if not switched:
        out = Filter(**{k: getattr(conv, k) for k in conv.__dataclass_fields__})
        # 会话里点名过的品牌与照片里的品牌不同(之前问 "苹果耳机",现在拍了一副
        # 索尼):照片优先,丢掉继承的品牌限定(本轮文字自己点名的品牌保留)。
        if out.brand_include and not (turn_filter and turn_filter.brand_include):
            if not _brand_hit(anchor, _alias_terms(out.brand_include)):
                out.brand_include = None
        return out, False
    t = turn_filter
    return Filter(
        category=t.category if t else None,
        sub_category=t.sub_category if t else None,
        sub_categories=list(t.sub_categories) if t and t.sub_categories else None,
        brand_include=list(t.brand_include) if t and t.brand_include else None,
        brand_exclude=list(conv.brand_exclude) if conv.brand_exclude else None,
        exclude_keywords=list(conv.exclude_keywords) if conv.exclude_keywords else None,
        price_max_cny=t.price_max_cny if t else None,
        price_min_cny=t.price_min_cny if t else None,
    ), True


def _price_cny(p: dict) -> float | None:
    raw = p.get("price_cny")
    if raw is None:
        cur = str((p.get("provenance") or {}).get("currency", "CNY")).upper()
        if cur != "CNY":
            return None
        raw = p.get("base_price")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _fmt_price(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 0.005 else f"{v:.2f}"


def relative_bounds(relative: str | None, anchor: dict | None, flt: Filter | None) -> tuple[float | None, float | None]:
    """把 "便宜点 / 贵一点" 换成相对锚点的人民币上 / 下限。用户写了具体金额时以金额为准。"""
    if not relative or anchor is None:
        return None, None
    if flt is not None and flt.has_price_constraint:
        return None, None
    base = _price_cny(anchor)
    if base is None:
        return None, None
    if relative == "cheaper":
        return round(base - 0.01, 2), None
    return None, round(base + 0.01, 2)


def apply_constraints(
    products: list[dict],
    flt: Filter | None,
    neg: LocalNegation,
    *,
    rel_max: float | None = None,
    rel_min: float | None = None,
) -> list[dict]:
    """严格执行(不做 fail-soft:筛空就是筛空,由调用方走回退)。products 需已做汇率归一化。"""
    out = list(products)
    if flt is not None and flt.active:
        out = apply_product_filter(out, flt, strict_cny_price=True)
        # product_matches_filter 的 brand_exclude 是精确匹配;这里补上别名
        # ("不要 Apple" 也要排除 "Apple 苹果")。
        if flt.brand_exclude:
            terms = _alias_terms(flt.brand_exclude)
            out = [p for p in out if not _brand_hit(p, terms)]
    if rel_max is not None:
        out = [p for p in out if (v := _price_cny(p)) is not None and v <= rel_max]
    if rel_min is not None:
        out = [p for p in out if (v := _price_cny(p)) is not None and v >= rel_min]
    out = apply_local_negation(out, neg, extra_country_keywords=(flt.exclude_keywords if flt else None))
    return out


def apply_local_negation(products: list[dict], neg: LocalNegation | None, *,
                         extra_country_keywords: Sequence[str] | None = None) -> list[dict]:
    """本地否定规则的严格版本(文字路径里 国产 / X以外 是 fail-soft 的,这里不是)。"""
    out = list(products)
    if neg is None:
        neg = LocalNegation()
    kws = list(dict.fromkeys([*(neg.country_keywords or []), *(extra_country_keywords or [])]))
    if neg.exclude_brands or kws:
        try:
            from rag.retrieve.negation import apply_negation
            out = apply_negation(out, {
                "exclude_brands": list(neg.exclude_brands),
                "exclude_categories": [],
                "exclude_keywords": kws,
            })
        except Exception:
            pass
    if neg.exclude_brands:
        terms = _alias_terms(neg.exclude_brands)
        out = [p for p in out if not _brand_hit(p, terms)]
    if neg.except_brands:
        terms = _alias_terms(neg.except_brands)
        out = [p for p in out if not _brand_hit(p, terms)]
    if neg.requires_domestic:
        try:
            from rag.retrieve.brand_origin import product_origin
            out = [p for p in out if (product_origin(p) or "CN") == "CN"]
        except Exception:
            pass
    if neg.title_keywords:
        low_kws = [k.lower() for k in neg.title_keywords]
        out = [p for p in out if not any(k in str(p.get("title") or "").lower() for k in low_kws)]
    return out


def enforced_labels(
    flt: Filter | None,
    neg: LocalNegation,
    *,
    rel_max: float | None,
    rel_min: float | None,
    anchor: dict | None,
) -> list[str]:
    """给 LLM 的"本轮卡片已强制满足的条件"清单(中文短语)。"""
    labels: list[str] = []
    if flt is not None:
        subs = _subs(flt)
        if subs:
            labels.append(f"品类限定为「{'/'.join(subs)}」")
        elif flt.category:
            labels.append(f"品类限定为「{flt.category}」")
        if flt.brand_include:
            labels.append(f"只要品牌「{'、'.join(flt.brand_include)}」")
        if flt.effective_price_min_cny is not None and flt.effective_price_max_cny is not None:
            labels.append(f"价格在 ¥{_fmt_price(flt.effective_price_min_cny)}–¥{_fmt_price(flt.effective_price_max_cny)} 之间(按人民币)")
        elif flt.effective_price_max_cny is not None:
            labels.append(f"价格不超过 ¥{_fmt_price(flt.effective_price_max_cny)}(按人民币)")
        elif flt.effective_price_min_cny is not None:
            labels.append(f"价格不低于 ¥{_fmt_price(flt.effective_price_min_cny)}(按人民币)")
    brands = list(dict.fromkeys([*((flt.brand_exclude or []) if flt else []),
                                 *neg.exclude_brands, *neg.except_brands]))
    if brands:
        labels.append(f"排除品牌「{'、'.join(brands)}」")
    kws = list(dict.fromkeys([*neg.country_keywords, *((flt.exclude_keywords or []) if flt else [])]))
    if kws:
        labels.append(f"排除「{'、'.join(kws)}」产地的品牌")
    if neg.requires_domestic:
        labels.append("只要国产品牌")
    if neg.title_keywords:
        labels.append(f"排除标题含「{'、'.join(neg.title_keywords)}」的商品")
    if anchor is not None and (rel_max is not None or rel_min is not None):
        base = _price_cny(anchor)
        word = "便宜" if rel_max is not None else "贵"
        if base is not None:
            labels.append(f"比图中最相似的「{(anchor.get('title') or '')[:24]}」(¥{_fmt_price(base)})更{word}")
    return labels


# ---------------------------------------------------------------------------
# 文字融合排序
# ---------------------------------------------------------------------------

RerankFn = Callable[[str, list[dict]], "dict[str, float]"]


def default_rerank(query: str, products: list[dict]) -> dict[str, float]:
    """本地交叉编码器(rag.retrieve.rerank,bge-reranker,sigmoid 分 ∈ [0,1])。
    在拷贝上打分,不污染调用方的 _retrieval;失败返回 {} → 保持视觉顺序。"""
    from rag.retrieve.rerank import rerank

    copies = [{**p, "_retrieval": dict(p.get("_retrieval") or {})} for p in products]
    ranked = rerank(query, copies, top_k=len(copies))
    out: dict[str, float] = {}
    for p in ranked:
        s = (p.get("_retrieval") or {}).get("rerank_score")
        if s is not None and p.get("product_id"):
            out[p["product_id"]] = float(s)
    return out


def fuse_scores(sims: dict[str, float], text_scores: dict[str, float], weight: float) -> dict[str, float]:
    """融合公式:final = clip_sim + λ · ce_score。

    * clip_sim:CLIP 余弦相似度(视觉,主信号);
    * ce_score:交叉编码器 sigmoid 分,∈ [0,1];
    * λ = IMAGE_TEXT_WEIGHT(默认 0.10)。

    文字分最多贡献 λ,所以只能在视觉相似度相差 < λ 的候选之间调整先后——
    视觉上明显更像的商品不会被文字翻盘。缺文字分的候选按 0 计。
    文字分先截到 [0,1]:换了一个不带 sigmoid 的交叉编码器(输出 logit)时,
    "文字最多贡献 λ" 这条保证也不会被打破。
    """
    def _clip01(v) -> float:
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if v != v else min(1.0, max(0.0, v))

    return {pid: s + weight * _clip01(text_scores.get(pid, 0.0)) for pid, s in sims.items()}


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------


def _identity(products: list[dict]) -> list[dict]:
    return [dict(p) for p in products]


def _default_normalize() -> Callable[[list[dict]], list[dict]]:
    try:
        from app.services.currency import normalize_product_prices
        return normalize_product_prices
    except Exception:
        return _identity


def _with_signal(p: dict, sim: float) -> dict:
    q = dict(p)
    sig = dict(q.get("_retrieval") or {})
    sig["source"] = "image"
    sig["clip_sim"] = round(float(sim), 4)
    q["_retrieval"] = sig
    return q


def anchor_kind(anchor: dict | None) -> list[str]:
    """锚点的"同一种东西":它的细分品类,加上 constraints._SUB_CATEGORY_RULES 里
    与它同组的窄组(≤4 个细分品类,如 耳机 = 无线降噪耳机/真无线耳机/真无线降噪耳机,
    面霜 = 面霜/面霜敏感肌)。"鞋子/上衣"这种宽组不并进来。"""
    sub = (anchor or {}).get("sub_category")
    if not sub:
        return []
    kind = {sub}
    try:
        from rag.retrieve.constraints import _SUB_CATEGORY_RULES
        for _terms, values in _SUB_CATEGORY_RULES:
            if sub in values and len(values) <= 4:
                kind |= set(values)
    except Exception:
        pass
    return sorted(kind)


def pin_to_anchor_category(products: list[dict], sims: dict[str, float], anchor: dict,
                           flt: Filter | None, *, strict_kind: bool = False) -> list[dict]:
    """视觉候选只保留与锚点同大类或同细分品类的商品(跑步鞋同时挂在 服饰运动 和
    户外运动 下,所以细分品类相同也算)。下限放得低(0.51),top-20 里常混进外观
    相近的别类商品。相似度与锚点相差 < IMAGE_CROSS_CAT_MARGIN 的近似并列不受限制。

    strict_kind:用户在要**替代品**(便宜点 / N元以内 / 不要这个牌子…)时,
    要的是同一种东西,收紧到锚点的细分品类族(anchor_kind),不留近似并列的口子。
    用户文字自己指定了品类(Filter 里有品类)时由 Filter 决定,这里不钉。"""
    margin = cross_cat_margin()
    if margin < 0 or not products:
        return products
    if flt is not None and (flt.category or _subs(flt)):
        return products
    a_cat, a_sub = anchor.get("category"), anchor.get("sub_category")
    kind = anchor_kind(anchor) if strict_kind else []
    if kind:
        return [p for p in products if p.get("sub_category") in kind]
    top = max(sims.values()) if sims else 0.0
    return [
        p for p in products
        if (a_cat and p.get("category") == a_cat)
        or (a_sub and p.get("sub_category") == a_sub)
        or sims.get(p.get("product_id"), 0.0) >= top - margin
    ]


def build_fallback(
    flt: Filter | None,
    neg: LocalNegation,
    anchor: dict,
    *,
    rel_max: float | None,
    rel_min: float | None,
    relative: str | None,
    text: str,
) -> tuple[Filter, str, str]:
    """约束清空视觉候选时的文字检索回退:同一组约束 + 锚点的品类 / 细分品类
    (本轮文字自己指定了品类时以文字为准)。返回 (filter, 检索 query, intent_text)。
    query / intent 里**不放否定词**——排除条件已经在 Filter 里。调用方另外以
    top_k(llm_free=True) 检索,两层一起保证不会走到 extract_negation 的 LLM 分支。"""
    has_own_cat = bool(flt is not None and (flt.category or _subs(flt)))
    if has_own_cat:
        category, subs = flt.category, (_subs(flt) or None)
    else:
        category = anchor.get("category") or None
        subs = anchor_kind(anchor) or None
    brand_exclude = list(dict.fromkeys([
        *((flt.brand_exclude or []) if flt else []),
        *_catalog_brands_matching([*neg.exclude_brands, *neg.except_brands]),
    ]))
    keywords = list(dict.fromkeys([
        *((flt.exclude_keywords or []) if flt else []),
        *neg.country_keywords, *neg.title_keywords,
    ]))
    fb = Filter(
        category=category,
        sub_categories=subs,
        brand_include=list(flt.brand_include) if flt and flt.brand_include else None,
        brand_exclude=brand_exclude or None,
        exclude_keywords=keywords or None,
        price_max_cny=(flt.effective_price_max_cny if flt else None) if rel_max is None else rel_max,
        price_min_cny=(flt.effective_price_min_cny if flt else None) if rel_min is None else rel_min,
    )
    head = ((None if has_own_cat else anchor.get("sub_category")) or (subs[0] if subs else None)
            or category or (anchor.get("title") or "")[:16])
    residual = descriptive_residual(text)
    query = " ".join(x for x in (head, residual, "国产" if neg.requires_domestic else "") if x).strip()
    # descriptive_residual 会删掉单字("是/个/的"…),删完可能**拼出**新的否定词
    # ("我不是要这个颜色" → "不要颜色")。回退检索的 top_k 已经是 llm_free,这里再兜一层:
    # query 里绝不留否定触发词,免得本地否定把拼出来的词当成排除条件。
    query = strip_negation_triggers(query)
    intent = query
    if relative == "cheaper":
        intent = f"{query} 更便宜"
    elif relative == "pricier":
        intent = f"{query} 更高端"
    return fb, query, strip_negation_triggers(intent)


# 与 rag_client._negation_signals / negation.extract_negation 的触发词保持一致
# (中文按子串;英文 "no " / "without" 按整词)。
_EN_NEG_TRIGGER_RE = re.compile(r"\bwithout\b|\bno\s", re.IGNORECASE)


def strip_negation_triggers(text: str) -> str:
    """反复删除否定触发词,直到一个都不剩(删掉一个可能又拼出另一个)。"""
    s = text or ""
    for _ in range(10):
        before = s
        for w in _NEG_TRIGGERS:
            s = s.replace(w, "")
        s = _EN_NEG_TRIGGER_RE.sub(" ", s)
        if s == before:
            break
    return re.sub(r"\s+", " ", s).strip()


def _catalog_brands_matching(names: Sequence[str]) -> list[str]:
    """把 "Apple" 这类品牌名展开成目录里实际的品牌串("Apple 苹果"),
    因为 Filter.brand_exclude 在检索层是精确匹配。"""
    if not names:
        return []
    terms = _alias_terms(names)
    try:
        from rag.retrieve.constraints import _catalog_brands
        catalog = _catalog_brands()
    except Exception:
        catalog = ()
    out = [b for b in catalog if _brand_hit({"brand": b}, terms)]
    return list(dict.fromkeys([*names, *out]))


def fuse_image_candidates(
    visual: Iterable,
    text: str,
    *,
    turn_filter: Filter | None = None,
    conversation_filter: Filter | None = None,
    k: int | None = None,
    floor: float | None = None,
    weight: float | None = None,
    rerank_fn: RerankFn | None = None,
    normalize_fn: Callable[[list[dict]], list[dict]] | None = None,
) -> ImageFusionResult:
    """给定视觉候选(已合并或 Hit 列表)与本轮文字,选出要出的卡。纯计算、不调 LLM。"""
    k = k or image_top_k()
    floor = min_sim() if floor is None else floor
    weight = text_weight() if weight is None else weight
    normalize = normalize_fn or _default_normalize()
    cands = visual if (isinstance(visual, list) and all(isinstance(c, VisualCandidate) for c in visual)) \
        else merge_visual_hits([visual])
    cands = sorted(cands, key=lambda c: c.sim, reverse=True)
    trace: dict = {"n_visual": len(cands), "floor": floor}
    if not cands:
        return ImageFusionResult(status="no_visual", floor=floor, trace=trace)

    top_sim = cands[0].sim
    above = [c for c in cands if c.sim >= floor]
    trace.update(top_sim=round(top_sim, 4), n_above_floor=len(above))
    text_n = normalize_question_forms(text or "")
    neg = local_negation(text_n)
    if not above:
        return ImageFusionResult(status="below_floor", top_sim=top_sim, floor=floor,
                                 negation=neg, trace=trace)

    sims = {c.product_id: c.sim for c in above}
    products = [_with_signal(p, sims[p.get("product_id")])
                for p in normalize([c.product for c in above])]
    anchor = products[0]
    relative = relative_price_direction(text_n)
    flt, dropped = effective_filter(turn_filter, conversation_filter, anchor,
                                    asked_brands=identification_brands(text or ""))
    rel_max, rel_min = relative_bounds(relative, anchor, flt)
    wants_alternative = bool(relative or neg.active or (flt is not None and flt.active))
    products = pin_to_anchor_category(products, sims, anchor, flt, strict_kind=wants_alternative)
    trace["n_after_category_pin"] = len(products)
    survivors = apply_constraints(products, flt, neg, rel_max=rel_max, rel_min=rel_min)
    labels = enforced_labels(flt, neg, rel_max=rel_max, rel_min=rel_min, anchor=anchor)
    anchor_excluded = anchor.get("product_id") not in {p.get("product_id") for p in survivors}
    trace.update(n_survivors=len(survivors), relative=relative, history_dropped=dropped,
                 enforced=labels)

    if not survivors:
        fb, q, intent = build_fallback(flt, neg, anchor, rel_max=rel_max, rel_min=rel_min,
                                       relative=relative, text=text_n)
        return ImageFusionResult(
            status="constraints_emptied", enforced=labels, anchor=anchor, anchor_excluded=True,
            top_sim=top_sim, floor=floor, negation=neg, fallback_filter=fb, fallback_query=q,
            fallback_intent=intent, history_dropped=dropped, trace=trace,
        )

    reordered = False
    if len(survivors) > 1 and has_descriptive_intent(text_n):
        query = strip_constraint_phrases(text_n)
        try:
            scores = (rerank_fn or default_rerank)(query, survivors) if query else {}
        except Exception:
            scores = {}
        if scores:
            fused = fuse_scores({p["product_id"]: sims[p["product_id"]] for p in survivors},
                                scores, weight)
            order = {pid: i for i, pid in enumerate(p["product_id"] for p in survivors)}
            new = sorted(survivors, key=lambda p: (-fused[p["product_id"]], order[p["product_id"]]))
            for p in new:
                p["_retrieval"]["text_score"] = round(float(scores.get(p["product_id"], 0.0)), 4)
                p["_retrieval"]["fused_score"] = round(fused[p["product_id"]], 4)
            reordered = [p["product_id"] for p in new] != [p["product_id"] for p in survivors]
            survivors = new
            trace["rerank_query"] = query

    return ImageFusionResult(
        status="visual", products=survivors[:k], enforced=labels, anchor=anchor,
        anchor_excluded=anchor_excluded, reordered_by_text=reordered, top_sim=top_sim,
        floor=floor, negation=neg, history_dropped=dropped, trace=trace,
    )
