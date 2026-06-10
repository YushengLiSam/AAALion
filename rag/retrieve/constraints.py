"""把用户的正向购物约束解析为检索期的元数据过滤条件。

这里只处理硬约束,例如商品品类、点名的品牌、人民币预算。
语义化偏好("适合熬夜"、"性价比")仍留在查询和重排器(reranker)里处理。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from rag.retrieve.query import Filter

REPO_ROOT = Path(__file__).resolve().parents[2]

_PRICE_MAX_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?\s*(?:以内|以下|内|封顶|不超过|不要超过)")
# R13 — 限定词在前的说法("不超过300" / "价格不要超过300" / "最多500块")。
# 上面那个"数字在前"的正则永远匹配不到这种写法:它的 不超过/不要超过 分支
# 只在限定词跟在数字后面时才生效,而实际没人会那样说。
_PRICE_MAX_PREFIX_RE = re.compile(r"(?:不要?超过|最多|顶多)\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*(?:元|块)?")
_PRICE_MIN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?\s*(?:以上|起|起步)")
_BUDGET_MAX_RE = re.compile(
    r"(?:预算|价格上限|最高价)\s*(?:提高|增加|加|放宽|调整|调|改)?\s*"
    r"(?:到|至|为|成)?\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?",
    re.IGNORECASE,
)
# R12 — 英文预算表达("under ¥500"、"below 500"、"≤ 1000"),
# 让英文模式的查询和快捷回复按钮(quick-reply chips)也能按价格过滤。
_PRICE_MAX_EN_RE = re.compile(
    r"(?:under|below|less than|within|up to|no more than|cheaper than|max\.?|≤|<=?)\s*"
    r"[¥￥$]?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
# 末尾字符类排除了 的,保证否定修饰语后面的正向宾语不被误伤
# ("不要苹果的耳机" 绝不能把 耳机 排除掉)。R13 — 用顿号/和 连接的
# 否定品牌列表("不要索尼的、Bose的、苹果的")要求否定作用域能跨过
# 每个 "X的、" 片段,否则只有第一个品牌被排除,其余的会落进
# brand_include。列表片段要求分隔符出现在 的 之后,
# 所以裸的 "苹果的耳机" 形式仍会在 的 处截断。
_NEGATED_PREFIX_RE = re.compile(
    r"(?:不想要|不需要|不要|别要|别给我|不考虑|不选|不买|排除|除了|避开|no\s*|without\s*)"
    r"(?:[^，。；,;]{1,12}?的?\s*[、和或])*"
    # 要 同样会截断作用域:"不要苹果的、要华为的" 在 要 处转回正向。
    r"[^，。；,;的要]*$",
    re.IGNORECASE,
)
# 后缀式排除:品牌在前,句末跟一个打发式否定
# ("小米的就算了" / "苹果的就不看了" / "安热沙的不要")。与上面的前缀形式
# 和 以外/之外 都不同。匹配的是品牌名之后紧跟的那段文本。
_SUFFIX_DISMISS_RE = re.compile(
    r"^的?\s*(?:这个|那个|那款|的话)?\s*(?:就)?"
    r"(?:算了|不用了?|不考虑了?|不看了?|不喜欢|跳过|pass|不行了?|不要了)"
)
# 裸后缀 "X的不要" —— 仅当它位于子句末尾时才生效,这样 "苹果的不要太贵"
# (价格修饰)不会把用户正向点名的品牌错误地排除掉。
_SUFFIX_BUYAO_RE = re.compile(r"^的?\s*不要\s*(?=[，。,；;]|$)")


def _is_suffix_negated(text: str, end: int) -> bool:
    window = text[end:end + 12]
    return bool(_SUFFIX_DISMISS_RE.match(window) or _SUFFIX_BUYAO_RE.match(window))

_DIRECT_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("美妆护肤", ("美妆护肤", "护肤品", "护肤", "化妆品", "美妆")),
    ("数码电子", ("数码电子", "数码产品")),
    ("服饰运动", ("服饰运动", "运动服饰")),
    ("食品饮料", ("食品饮料",)),
    ("食品生活", ("食品生活",)),
    ("母婴健康", ("母婴健康", "母婴")),
    ("家居家具", ("家居家具", "家居")),
    ("图书音像", ("图书音像", "图书")),
    ("户外运动", ("户外运动", "户外")),
)

# 兜底类目推断:把查询固定到自己的"货架"上,让没有精确子类目规则的词
# (如 球衣 / 运动服 / 香薰)仍留在类目内,而不是泄漏到全部 145 个商品里。
# 这里只收录单一类目的词;跨货架的运动词(跑鞋/徒步鞋,横跨 服饰运动+户外运动)
# 有意不收录,这样它们的子类目规则可以同时返回两个货架。
_INFERRED_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("美妆护肤", ("洗面奶", "洁面", "防晒", "面霜", "精华", "化妆水", "爽肤水", "神仙水",
                  "口红", "唇釉", "唇膏", "唇彩", "粉底", "散粉", "蜜粉", "眼霜", "眉笔",
                  "卸妆", "面膜", "护肤", "彩妆")),
    ("数码电子", ("耳机", "手机", "折叠屏", "笔记本", "平板", "相机", "摄像机",
                  "游戏机", "游戏主机", "任天堂", "switch", "电脑",
                  # R12.bugfix — 英文/品牌产品线名,让单轮查询或多轮开场白
                  # (如 "推荐 iPhone")固定到 数码电子,后续追问也继承该货架。
                  # 匹配经 casefold 后不区分大小写(见 _category),
                  # 所以这里写小写即可。
                  "iphone", "ipad", "airpods", "macbook", "apple watch")),
    ("服饰运动", ("卫衣", "羽绒", "球衣", "球服", "运动服", "运动上衣", "运动外套",
                  "牛仔裤", "瑜伽裤", "篮球鞋", "T恤", "上衣", "衣服", "服装",
                  "帽子", "卫裤", "短袖", "外套", "夹克")),
    ("母婴健康", ("奶粉", "纸尿裤", "尿不湿", "辅食", "米粉", "孕妇", "叶酸",
                  "蛋白粉", "婴儿", "母婴", "奶瓶")),
    # R13 — 已移除 香薰/香氛:商品库里没有任何香氛类商品,类目固定只会给
    # 这类查询圈出一堆无关的 家居 卡片;去掉过滤信号后,
    # 相关性闸门(relevance gate)现在会诚实地判为无匹配。
    ("家居家具", ("四件套", "床上用品", "床品", "被子", "枕头", "插线板", "插排",
                  "排插", "家居", "家具")),
    ("图书音像", ("小说", "漫画", "字典", "词典", "工具书", "科幻", "名著")),
    ("食品饮料", ("速溶咖啡", "咖啡", "牛奶", "酸奶", "泡面", "方便面", "功能饮料",
                  "碳酸饮料", "气泡水", "茶饮", "调味品", "酱油")),
)

# R8.F.8(R8.F.8.1 中扩充):仅用于话题切换检测的关键词。由 rag_client
# 的话题切换检测器检查,用来识别用户原始消息中的领域转向。
# 不参与硬过滤 —— 如果把它们加进上面的表,
# 单轮查询的检索范围会被收得过窄。
TOPIC_SWITCH_HINTS: dict[str, tuple[str, ...]] = {
    "美妆护肤": ("美妆", "化妆品", "彩妆", "口红"),
    "数码电子": ("电脑", "智能手表", "音箱"),
    "服饰运动": ("运动鞋", "跑鞋", "T恤", "外套", "裤子", "衣服", "夹克", "鞋",
                  "鞋子", "上衣", "短裤"),
    "食品饮料": ("饮料", "茶", "矿泉水"),
    "食品生活": ("零食", "巧克力", "饼干", "薯片", "洗衣液", "牙膏", "酱油"),
    "母婴健康": ("奶粉", "尿不湿", "纸尿裤", "纸尿片", "婴儿用品", "童装", "母婴"),
    "家居家具": ("沙发", "床", "餐具", "桌椅", "家具"),
    "图书音像": ("书", "教材"),
}


def detect_topic_switch_category(text: str) -> str | None:
    """若原始文本包含话题切换提示词,返回对应类目。
    供多轮对话的话题切换检测器使用 —— 不参与硬过滤条件的构建
    (否则会把单轮查询过度收窄)。
    """
    if not text:
        return None
    text = _with_english_hints(text)
    for cat, words in TOPIC_SWITCH_HINTS.items():
        if any(w in text for w in words):
            return cat
    return None


def _with_english_hints(text: str) -> str:
    """为英文购物词追加对应的中文类目词,让本模块的中文关键词表
    在英文查询上也能命中("noise cancelling headphones" 以前提取不到
    任何类目 —— 只剩价格条件的 WHERE 会返回一堆便宜的无关商品)。
    文本已含中文时不做任何处理。失败时静默降级(fail-soft)。"""
    try:
        from rag.retrieve.english_terms import english_to_chinese_hints

        hints = english_to_chinese_hints(text)
    except Exception:
        return text
    return f"{text} {' '.join(hints)}" if hints else text

# 用户的一个概念可能横跨多个源数据子类目。
# 排序遵循 具体 → 泛化:_sub_categories 返回第一条有词命中的规则,
# 所以具体词(篮球鞋)必须排在泛化词前面。规则表基于线上商品库的
# 分类体系(9 个类目 / 73 个 sub_category)构建,
# 让类目查询收窄到自己的货架,而不是跨类目泄漏。
_SUB_CATEGORY_RULES: tuple[tuple[tuple[str, ...], list[str]], ...] = (
    # —— 美妆护肤 ——
    (("口红", "唇釉", "唇膏", "唇彩"), ["唇釉"]),
    (("卸妆油", "卸妆水", "卸妆膏", "卸妆"), ["卸妆"]),
    (("洗面奶", "洁面乳", "洁面"), ["洁面"]),
    (("高倍防晒", "防晒霜", "防晒乳", "防晒"), ["防晒"]),
    (("眼霜", "眼部精华"), ["眼霜"]),
    (("眉笔", "眉粉"), ["眉笔"]),
    (("粉底液", "粉底", "气垫"), ["粉底液"]),
    (("蜜粉", "散粉", "定妆粉", "粉饼"), ["蜜粉"]),
    (("面膜",), ["面膜"]),
    (("面霜", "乳霜", "晚霜", "日霜"), ["面霜", "面霜/敏感肌"]),
    (("神仙水", "爽肤水", "化妆水", "柔肤水", "水乳"), ["化妆水", "化妆水/精华水", "爽肤水/化妆水", "精华水"]),
    (("精华液", "精华", "小黑瓶", "小棕瓶", "红腰子", "肌底液"), ["精华", "精华液", "精华水"]),
    # —— 数码电子 ——
    (("头戴式降噪耳机", "蓝牙耳机", "无线耳机", "降噪耳机", "耳机"), ["无线降噪耳机", "真无线耳机", "真无线降噪耳机"]),
    (("笔记本电脑", "笔记本"), ["笔记本电脑"]),
    (("折叠屏", "智能手机", "手机"), ["智能手机"]),
    (("平板电脑", "平板"), ["平板电脑"]),
    (("摄像机", "口袋相机", "云台相机", "运动相机", "相机"), ["口袋摄像机"]),
    (("游戏主机", "游戏机", "掌机", "switch", "任天堂"), ["游戏主机"]),
    # —— 服饰运动 / 户外运动(部分 sub 跨这两个类目)——
    (("连帽卫衣", "连帽衫", "帽衫", "卫衣"), ["卫衣"]),
    (("鸭舌帽", "棒球帽", "球帽", "帽子"), ["帽子"]),
    (("羽绒服", "羽绒衣", "羽绒"), ["羽绒服"]),
    (("抓绒外套", "抓绒", "软壳外套"), ["抓绒外套"]),
    (("冲锋衣", "三合一外套"), ["冲锋衣"]),
    (("牛仔裤",), ["牛仔裤"]),
    (("瑜伽裤", "紧身裤", "打底裤"), ["瑜伽裤"]),
    (("户外裤", "软壳裤"), ["户外裤"]),
    (("运动短裤", "短裤"), ["运动短裤"]),
    (("运动长裤", "卫裤", "束脚裤", "长裤"), ["运动长裤"]),
    (("运动裤", "休闲裤"), ["运动长裤", "运动短裤"]),
    (("速干T恤", "运动T恤", "运动t恤", "速干t恤", "T恤", "t恤", "短袖"), ["短袖T恤", "速干T恤"]),
    # R13.1 — 泛化的上装词放在具体上装规则之后。上衣 以前只有类目固定
    # (整个 服饰运动 货架),导致 "对比一下上衣" 时一条 运动长裤
    # 被重排进了上装卡片旁边。
    (("上衣",), ["卫衣", "羽绒服", "冲锋衣", "抓绒外套", "短袖T恤", "速干T恤"]),
    (("外套", "夹克"), ["冲锋衣", "抓绒外套", "羽绒服", "卫衣"]),
    (("篮球鞋",), ["篮球鞋"]),
    (("跑步鞋", "跑鞋", "马拉松鞋"), ["跑步鞋"]),
    (("板鞋", "休闲鞋", "复古鞋"), ["运动休闲鞋"]),
    (("登山鞋", "徒步鞋", "登山徒步鞋"), ["徒步鞋", "登山徒步鞋"]),
    # 泛化的鞋类词("挑双球鞋" / "运动鞋" / "鞋子")—— 排在具体鞋类规则之后,
    # 保证 篮球鞋/跑鞋/板鞋 仍优先命中。覆盖所有鞋类子类目;
    # 不做类目固定(跑步鞋 同时存在于 服饰运动 和 户外运动)。
    (("球鞋", "运动鞋", "鞋子"), ["篮球鞋", "跑步鞋", "运动休闲鞋", "徒步鞋", "登山徒步鞋"]),
    (("帐篷", "露营"), ["帐篷"]),
    (("双肩包", "背包", "登山包", "书包"), ["背包"]),
    (("智能手表", "运动手表", "手表"), ["运动手表"]),
    # —— 母婴健康 ——
    (("婴儿奶粉", "婴幼儿奶粉", "配方奶", "奶粉"), ["婴幼儿奶粉"]),
    (("婴儿辅食", "辅食", "米粉"), ["婴儿辅食"]),
    (("孕妇营养", "叶酸", "孕妇"), ["孕妇营养品"]),
    (("纸尿裤", "尿不湿", "尿布", "拉拉裤"), ["纸尿裤"]),
    (("蛋白粉",), ["蛋白粉"]),
    # —— 家居家具 ——
    (("四件套", "床上用品", "床品", "被套", "被子", "枕头"), ["床上用品"]),
    (("插线板", "插排", "排插", "接线板", "插座"), ["插线板"]),
    # R13 — 已删除 香薰→礼盒/家居香氛 规则:商品库里没有真正的香薰商品;
    # 该 sub_category 下唯一一条是被错误归类的 雪花秀 护肤礼盒,
    # 这条规则曾把它硬拉进香薰结果("文字说没有香薰,卡片却给洁面套装")。
    # 香薰 仍通过 _INFERRED_CATEGORIES 保留 家居家具 的类目固定,
    # 无匹配的情况交给相关性闸门处理。
    (("医用冰箱", "家用健康家电", "家电"), ["家用健康家电"]),
    # —— 食品饮料 ——
    (("功能饮料", "能量饮料"), ["功能饮料"]),
    (("速溶咖啡", "咖啡"), ["咖啡"]),
    (("方便面", "泡面", "拌面", "方便食品"), ["方便食品"]),
    (("酸奶",), ["酸奶"]),
    (("牛奶",), ["牛奶"]),
    (("碳酸饮料", "汽水", "可乐", "气泡水", "苏打水", "苏打"), ["碳酸饮料"]),
    (("茶饮", "茶"), ["茶饮"]),
    (("调味品", "酱油", "生抽", "老抽", "蚝油"), ["调味品"]),
    # —— 食品生活(进口)——
    (("巧克力",), ["进口巧克力零食"]),
    (("饼干",), ["进口零食饼干"]),
    (("蛋黄酱", "沙拉酱", "酱料"), ["进口酱料调味"]),
    (("调味料", "百吉饼"), ["进口调味料"]),
    (("乳酸菌饮料", "乳酸饮料", "可尔必思"), ["进口饮料"]),
    (("坚果", "休闲零食", "零食"), ["坚果/零食", "进口巧克力零食", "进口零食饼干"]),
    # —— 图书音像 ——
    (("科幻小说", "科幻", "三体"), ["中文小说/科幻"]),
    (("名著", "经典文学", "活着", "平凡的世界"), ["中文小说/经典"]),
    (("小说", "文学"), ["中文小说/科幻", "中文小说/经典"]),
    (("字典", "词典", "工具书"), ["工具书"]),
    (("漫画", "哆啦", "连环画"), ["漫画"]),
)


def build_retrieval_filter(text: str, explicit: Mapping[str, Any] | None = None) -> Filter | None:
    """从查询文本推断过滤条件,再应用 API 显式传入的覆盖项。"""
    result = Filter(
        category=_category(text),
        sub_categories=_sub_categories(text),
        brand_include=[],
        brand_exclude=[],
        price_max_cny=(_price_bound(_PRICE_MAX_RE, text) or _price_bound(_PRICE_MAX_PREFIX_RE, text)
                       or _price_bound(_BUDGET_MAX_RE, text) or _price_bound(_PRICE_MAX_EN_RE, text)),
        price_min_cny=_price_bound(_PRICE_MIN_RE, text),
    )
    included, excluded = _brands(text)
    result.brand_include = included or None
    result.brand_exclude = excluded or None

    # 类目冲突保护:场景/属性词推断出的类目可能与更具体的 sub_category
    # 或点名品牌相矛盾 —— 例如 "对比防晒...哪个更适合户外" 推断出 户外运动,
    # 而 防晒 属于 美妆护肤,于是 AND 过滤(category × sub)返回 0 条。
    # 以更具体的信号为准:当商品库中不存在该 (category, sub_category) 组合,
    # 或(没有子类目时)点名品牌都不属于该类目,就丢弃推断出的类目。
    if result.category:
        pairs = _catalog_cat_subcats()
        bcats = _catalog_brand_cats()
        sub_conflict = bool(result.sub_categories) and not any(
            (result.category, sc) in pairs for sc in result.sub_categories
        )
        brand_conflict = bool(result.brand_include) and not any(
            result.category in bcats.get(str(b).casefold(), frozenset()) for b in result.brand_include
        )
        if sub_conflict or (not result.sub_categories and brand_conflict):
            result.category = None

    # R8:在本地提取国别触发词("日系" / "美系" / ...),
    # 让它们通过 Filter 状态在多轮对话间持久保留。
    # `apply_negation` 会经由 brand_origin 把这些词解析为 ISO 国家码。
    if text and any(neg in text for neg in (
        "不要", "别要", "别给我", "不想要", "不需要", "不考虑", "不含", "不带",
        "除了", "排除", "也不要", "就算了", "就不看", "不用了"
    )):
        try:
            from rag.retrieve.negation import _local_country_keywords
            result.exclude_keywords = _local_country_keywords(text) or None
        except Exception:
            result.exclude_keywords = None

    if explicit:
        if explicit.get("category") is not None:
            result.category = str(explicit["category"])
        if explicit.get("sub_category") is not None:
            result.sub_category = str(explicit["sub_category"])
            result.sub_categories = None
        if explicit.get("sub_categories") is not None:
            result.sub_category = None
            result.sub_categories = list(explicit["sub_categories"]) or None
        if explicit.get("include_brands") is not None:
            result.brand_include = list(explicit["include_brands"]) or None
        if explicit.get("exclude_brands") is not None:
            result.brand_exclude = list(explicit["exclude_brands"]) or None
        if explicit.get("price_min") is not None:
            result.price_min_cny = float(explicit["price_min"])
        if explicit.get("price_max") is not None:
            result.price_max_cny = float(explicit["price_max"])
    return result if result.active else None


def _category(text: str) -> str | None:
    # 用 casefold 让英文/品牌关键词("iphone"、"switch"、"T恤")
    # 不受用户输入大小写("iPhone"、"Switch"、"iPHONE")影响,都能匹配。
    low = _with_english_hints(text or "").casefold()
    for category, terms in _DIRECT_CATEGORIES:
        if any(term.casefold() in low for term in terms):
            return category
    for category, terms in _INFERRED_CATEGORIES:
        if any(term.casefold() in low for term in terms):
            return category
    return None


def _sub_categories(text: str) -> list[str] | None:
    low = _with_english_hints(text or "").casefold()
    for terms, values in _SUB_CATEGORY_RULES:
        if any(term.casefold() in low for term in terms):
            return list(values)
    return None


def _price_bound(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text or "")
    return float(match.group(1)) if match else None


@lru_cache(maxsize=1)
def _catalog_brands() -> tuple[str, ...]:
    brands: set[str] = set()
    for path in (REPO_ROOT / "data" / "seed").glob("*/data/*.json"):
        try:
            product = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        brand = product.get("brand")
        if isinstance(brand, str) and brand.strip():
            brands.add(brand.strip())
    return tuple(sorted(brands, key=len, reverse=True))


@lru_cache(maxsize=1)
def _catalog_cat_subcats() -> frozenset[tuple[str, str]]:
    """商品库中真实存在的 (category, sub_category) 组合 ——
    用于检测推断出的类目是否与更具体的 sub_category 相矛盾
    (例如 '防晒' 属于 美妆护肤,绝不会是 户外运动)。"""
    pairs: set[tuple[str, str]] = set()
    for path in (REPO_ROOT / "data" / "seed").glob("*/data/*.json"):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        c, s = p.get("category"), p.get("sub_category")
        if isinstance(c, str) and isinstance(s, str):
            pairs.add((c.strip(), s.strip()))
    return frozenset(pairs)


@lru_cache(maxsize=1)
def _catalog_brand_cats() -> dict[str, frozenset[str]]:
    """品牌(casefold 后)→ 该品牌出现过的类目集合。"""
    out: dict[str, set[str]] = {}
    for path in (REPO_ROOT / "data" / "seed").glob("*/data/*.json"):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        b, c = p.get("brand"), p.get("category")
        if isinstance(b, str) and isinstance(c, str):
            out.setdefault(b.strip().casefold(), set()).add(c.strip())
    return {k: frozenset(v) for k, v in out.items()}


def _brand_terms(catalog_brand: str) -> set[str]:
    from rag.retrieve.brand_origin import BRAND_ALIASES

    lowered = catalog_brand.casefold()
    terms = {lowered}
    for cluster in BRAND_ALIASES:
        if any(alias in lowered or lowered in alias for alias in cluster):
            terms.update(alias.casefold() for alias in cluster)
    return {term for term in terms if len(term) >= 2}


def _find_brand_term(lowered: str, term: str) -> int:
    """返回品牌别名在文本中的位置,找不到返回 -1。ASCII 别名必须落在字母边界上:
    短别名按裸子串匹配时,曾给英文查询钉上无关品牌("progra-mi-ng" →
    brand_include=['小米'],随后的 AND 过滤把 "laptop ... programming"
    筛到 0 条结果)。数字仍允许紧邻,所以 "iphone13" 依然能匹配 "iphone"。"""
    if term.isascii():
        m = re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", lowered)
        return m.start() if m else -1
    return lowered.find(term)


def _brands(text: str) -> tuple[list[str], list[str]]:
    lowered = (text or "").casefold()
    included: list[str] = []
    excluded: list[str] = []
    for catalog_brand in _catalog_brands():
        hits = [
            (pos, len(term))
            for term in _brand_terms(catalog_brand)
            if (pos := _find_brand_term(lowered, term)) >= 0
        ]
        if not hits:
            continue
        # 只要任一处出现被否定就排除该品牌:前缀否定("不要X"/"别给我X")、
        # 以外/之外 后缀,或句末打发式否定("X的就算了"/"X的不要")。
        is_excluded = any(
            _is_negated(lowered, pos) or _is_suffix_negated(lowered, pos + tlen)
            for pos, tlen in hits
        )
        target = excluded if is_excluded else included
        target.append(catalog_brand)
    excluded_set = set(excluded)
    return ([brand for brand in included if brand not in excluded_set], excluded)


def _is_negated(text: str, position: int) -> bool:
    # 取 30 字符窗口,保证否定动词在品牌列表中仍然可见
    # ("不要索尼的、Bose的、苹果的、华为的" —— 华为 离 不要 超过 18 个字符)。
    # 正则字符类里的标点仍会在子句边界处重置否定作用域。
    prefix = text[max(0, position - 30):position]
    if _NEGATED_PREFIX_RE.search(prefix):
        return True
    # R11.fix — "X以外 / X之外"(如多轮里的 "华为以外还有吗"):品牌其实是
    # 被排除的对象,但标记词是后缀,没有这段逻辑时会被误读成正向的
    # brand_include(→ 只检索 华为)。在品牌之后的一小段窗口内
    # 查找 以外/之外 标记。
    suffix = text[position:position + 10]
    return "以外" in suffix or "之外" in suffix
