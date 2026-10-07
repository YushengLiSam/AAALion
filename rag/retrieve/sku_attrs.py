"""SKU 规格问题("有没有黑色的 / 有XL码吗 / 有256G的吗")的确定性解析与匹配,不调 LLM。

拍照找货里,"图里这件有没有黑色款"只能查结构化 SKU:每个商品只有一张图,图里只看得到一种颜色;
离线图片描述(image_caption)也只描述这张图。所以这里只读商品 JSON 的 ``skus[].properties``。

规格族(family)与归一化(保守:宁可说"没有/不确定",不把相近的值硬算成一样):

* ``color``    键名含"色"(颜色 / 机身颜色 / 配色 / 帽身颜色),不含"色号"和 logo。
  值归到**主色**:取值里第一个基础色字(黑白灰蓝绿红粉紫黄橙棕银金米),"藏青"算蓝、"咖啡"算棕;
  经典黑 / 深空黑 / 暗夜黑实战色 → 黑;黑色白三条纹 → 黑(第一个颜色为主色);
  星光色 / 午夜色 / 经典裸色这类没有基础色字的值不归类(问"白色的"不会命中"星光色")。
  用户问基础色("黑色的 / 黑的")→ 主色相同即命中;问具体色名("经典黑 / 深蓝色 / 浅蓝色")
  → 值里必须含这个色名(去掉末尾"色"),"深蓝色"不会命中"藏青色"。
* ``size``     键名含"码"(尺码 / 鞋码)。字母码 S/M/L/XL/XXL/XXXL(2XL=XXL)、数字码 28–46(含 .5)。
  单个字母(S/M/L)必须带"码/号"才算("L码"),避免把英文缩写当尺码。
* ``storage``  键名含"存储"或"硬盘",或"内存组合"。只比机身存储(ROM):"12GB+256GB" 取 256GB,
  "512GB SSD" 取 512GB。问法只认 64/128/256/512 G(B) 与 1/2/4 T(B),"500g" 是克,不算。
* ``capacity`` 任何键里以体积开头的值("50ml 加大装" / "1.9L" / "20L"),统一成毫升比较。
* ``screen``   键名含"尺寸"(屏幕尺寸 / 尺寸)的英寸数;"裤长 25英寸"不算。

``product_has`` 返回三态:True(SKU 里有)/ False(该商品**列了**这一族规格但没有这个值)/
None(该商品根本没列这一族规格——不能说"没有",只能说"SKU 数据没写")。

文字路径(纯文字请求)暂不使用本模块:见 docs/IMAGE_SEARCH.md 的"下一步"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

FAMILY_LABELS = {
    "color": "颜色",
    "size": "尺码",
    "storage": "存储容量",
    "capacity": "容量",
    "screen": "屏幕尺寸",
}

_BASE_COLORS = "黑白灰蓝绿红粉紫黄橙棕银金米"
_COLOR_ALIASES = (("藏青", "蓝"), ("咖啡", "棕"))
_LETTER_SIZES = ("XXXL", "XXL", "XL", "XS", "L", "M", "S")


@dataclass(frozen=True)
class AttrAsk:
    family: str          # color / size / storage / capacity / screen
    value: str           # 归一化后的值(color 基础色为单字,具体色名为色名本身)
    label: str           # 给 LLM / UI 的写法:"黑色" / "XL码" / "256GB" / "500ml" / "14英寸"
    specific: bool = False  # color 专用:True = 具体色名(经典黑 / 深蓝色),False = 基础色

    @property
    def family_label(self) -> str:
        return FAMILY_LABELS.get(self.family, self.family)


# ---------------------------------------------------------------------------
# 值的归一化(商品侧)
# ---------------------------------------------------------------------------


def _cjk(s: str) -> str:
    return "".join(re.findall(r"[一-鿿]+", s or ""))


def color_family(value: str) -> str | None:
    """颜色值 → 主色(单字);没有基础色字时返回 None。"""
    s = _cjk(value)
    for src, dst in _COLOR_ALIASES:
        s = s.replace(src, dst)
    for ch in s:
        if ch in _BASE_COLORS:
            return ch
    return None


_SIZE_LETTER_VALUE_RE = re.compile(r"^\s*(XXXL|XXL|XL|XS|L|M|S)\s*(?:码|号)?\s*$", re.IGNORECASE)
_SIZE_NUM_VALUE_RE = re.compile(r"^\s*(\d{2}(?:\.5)?)\s*码\s*$")


def size_value(value: str) -> str | None:
    m = _SIZE_LETTER_VALUE_RE.match(value or "")
    if m:
        return m.group(1).upper()
    m = _SIZE_NUM_VALUE_RE.match(value or "")
    if m:
        return m.group(1)
    if (value or "").strip() == "均码":
        return "均码"
    return None


_STORAGE_TOKEN_RE = re.compile(r"(\d+)\s*(GB|TB|G|T)(?![A-Za-z])", re.IGNORECASE)


def storage_value(value: str) -> str | None:
    """机身存储:取最后一个容量记号("12GB+256GB" → 256GB)。"""
    toks = _STORAGE_TOKEN_RE.findall(value or "")
    if not toks:
        return None
    num, unit = toks[-1]
    unit = unit.upper()
    unit = "GB" if unit in ("G", "GB") else "TB"
    return f"{int(num)}{unit}"


_VOLUME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|毫升|l|升)(?![A-Za-z])", re.IGNORECASE)


def _ml(num: str, unit: str) -> float:
    v = float(num)
    return v * 1000 if unit.lower() in ("l", "升") else v


def capacity_value(value: str) -> str | None:
    """以体积开头的值 → "500ml"(统一成毫升,去掉多余的 .0)。"""
    m = _VOLUME_RE.match((value or "").strip())
    if not m:
        return None
    v = _ml(m.group(1), m.group(2))
    return f"{v:g}ml"


_SCREEN_RE = re.compile(r"(\d{1,2}(?:\.\d)?)\s*(?:英寸|寸|inch(?:es)?|\")", re.IGNORECASE)


def screen_value(value: str) -> str | None:
    m = _SCREEN_RE.search(value or "")
    return f"{float(m.group(1)):g}" if m else None


def _family_of_key(key: str) -> list[str]:
    k = key or ""
    fams: list[str] = []
    if "色" in k and "色号" not in k and "logo" not in k.lower():
        fams.append("color")
    if "码" in k:
        fams.append("size")
    if "存储" in k or "硬盘" in k or k == "内存组合":
        fams.append("storage")
    if "尺寸" in k:
        fams.append("screen")
    if not fams:
        fams.append("capacity")  # 只有能解析成体积的值才会真正记进去
    return fams


def product_attr_values(product: dict) -> dict[str, list[str]]:
    """商品 SKU 规格 → {family: [原始值...]}(去重、保序)。只收能按该族解析的值。"""
    out: dict[str, list[str]] = {}
    for sku in (product or {}).get("skus") or []:
        for key, raw in ((sku or {}).get("properties") or {}).items():
            val = str(raw or "").strip()
            if not val:
                continue
            for fam in _family_of_key(str(key)):
                if fam == "color" or _NORMALIZERS[fam](val) is not None:
                    bucket = out.setdefault(fam, [])
                    if val not in bucket:
                        bucket.append(val)
    return out


_NORMALIZERS = {
    "size": size_value,
    "storage": storage_value,
    "capacity": capacity_value,
    "screen": screen_value,
}


def value_matches(ask: AttrAsk, raw_value: str) -> bool:
    if ask.family == "color":
        if ask.specific:
            token = ask.value[:-1] if ask.value.endswith("色") and len(ask.value) > 2 else ask.value
            return token in _cjk(raw_value)
        return color_family(raw_value) == ask.value
    norm = _NORMALIZERS[ask.family](raw_value)
    return norm is not None and norm == ask.value


def product_has(product: dict, ask: AttrAsk) -> bool | None:
    """True / False / None(该商品 SKU 里没有这一族规格)。"""
    vals = product_attr_values(product).get(ask.family)
    if not vals:
        return None
    return any(value_matches(ask, v) for v in vals)


def matching_values(product: dict, ask: AttrAsk) -> list[str]:
    """该商品里命中的原始值(给 LLM 附加段用,如 ["经典黑", "黑色"])。"""
    return [v for v in product_attr_values(product).get(ask.family, []) if value_matches(ask, v)]


# ---------------------------------------------------------------------------
# 目录词表
# ---------------------------------------------------------------------------


def _catalog_products() -> Iterable[dict]:
    try:
        from rag.retrieve.query import _product_index
        return _product_index().values()
    except Exception:
        return ()


@lru_cache(maxsize=1)
def catalog_vocabulary() -> dict[str, tuple[str, ...]]:
    """目录里出现过的 SKU 规格值,按族汇总(原始写法,去重)。"""
    vocab: dict[str, list[str]] = {}
    for p in _catalog_products():
        for fam, vals in product_attr_values(p).items():
            bucket = vocab.setdefault(fam, [])
            for v in vals:
                if v not in bucket:
                    bucket.append(v)
    return {fam: tuple(vs) for fam, vs in vocab.items()}


@lru_cache(maxsize=1)
def _specific_color_names() -> tuple[str, ...]:
    """目录里的具体色名(CJK 部分,≥2 字且不是"X色"这种基础色写法),长的在前。"""
    names: set[str] = set()
    for v in catalog_vocabulary().get("color", ()):
        c = _cjk(v)
        if len(c) < 2:
            continue
        if len(c) == 2 and c.endswith("色") and c[0] in _BASE_COLORS:
            continue  # 黑色 / 白色 / 蓝色:按基础色处理
        names.add(c)
    return tuple(sorted(names, key=len, reverse=True))


# ---------------------------------------------------------------------------
# 问法解析(文字侧)
# ---------------------------------------------------------------------------

_NEG_BEFORE_RE = re.compile(
    r"(?:不要|别要|不想要|不需要|不考虑|不买|不选|除了|排除|别给我|不喜欢|不是|不用)[^，。,;；!！?？]{0,6}$")
# 后置否定:"黑色的不要 / 黑色就算了 / 白色以外的 / XL码不考虑"
_NEG_AFTER_RE = re.compile(
    r"^(?:的|款|色|码|号)?\s*(?:不要|就算了|算了|不考虑|不行|不喜欢|不买|以外|之外|除外|pass)", re.IGNORECASE)
_COLOR_SUFFIX_RE = re.compile(r"([深浅亮暗淡]?)(?:([黑白灰蓝绿红粉紫黄橙棕银金])(色|的|款)|(米)(色))")
# 不带"色"字的基础色("黑的 / 白款")只在前一个字是这些(或句首)时才算颜色:
# 网红款 / 奶粉的 / 蛋白粉的 / 定金的 / 黄金的 / 明白的 都不是在问颜色。
_COLOR_BARE_PREV = set("有要是个种换选买拿找来看出纯全偏带那这哪啥么吗呢只款双件条，,。.、:： ")
_SIZE_LETTER_ASK_RE = re.compile(r"(?<![A-Za-z])(XXXL|XXL|XL|XS|2XL|3XL|L|M|S)\s*(码|号)", re.IGNORECASE)
_SIZE_MULTI_ASK_RE = re.compile(r"(?<![A-Za-z0-9])(XXXL|XXL|XL|XS|2XL|3XL)(?![A-Za-z0-9])", re.IGNORECASE)
# 不带"码"的多字母码前面紧挨着英文/数字词时是型号("Pixel 7 XL" / "iPhone XS"),不是尺码
_MODEL_BEFORE_RE = re.compile(r"[A-Za-z0-9]\s*$")
_SIZE_NUM_ASK_RE = re.compile(r"(?<![\d.])(\d{2}(?:\.5)?)\s*码")
# "64G运存 / 12G RAM" 是运行内存,不是机身存储;"4T恤" 是 T 恤
_STORAGE_ASK_RE = re.compile(
    r"(?<![\d.])(64|128|256|512)\s*(?:GB|G)(?![A-Za-z])(?!\s*(?:运存|运行内存|RAM))"
    r"|(?<![\d.])([124])\s*(?:TB|T)(?![A-Za-z恤])(?!\s*(?:运存|运行内存|RAM))",
    re.IGNORECASE,
)
_CAPACITY_ASK_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(ml|毫升|l|升)(?![A-Za-z])", re.IGNORECASE)
_SCREEN_ASK_RE = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d)?)\s*(?:英寸|寸)")


def _negated(text: str, start: int, end: int | None = None) -> bool:
    if _NEG_BEFORE_RE.search(text[:start]):
        return True
    return end is not None and bool(_NEG_AFTER_RE.search(text[end:]))


def _parse_color(text: str) -> AttrAsk | None:
    # 1) 目录里的具体色名(经典黑 / 深空黑 / 星光色 / 深蓝色 …)
    for name in _specific_color_names():
        i = text.find(name)
        if i >= 0 and not _negated(text, i, i + len(name)):
            return AttrAsk("color", name, name, specific=True)
    # 2) 基础色(可带深/浅修饰:"浅蓝色" 是具体色名,目录里没有就如实说没有)
    # "米" 只认 "米色"("大米的" 不是颜色)
    for m in _COLOR_SUFFIX_RE.finditer(text):
        mod, base, suffix = m.group(1), (m.group(2) or m.group(4)), (m.group(3) or m.group(5))
        if _negated(text, m.start(), m.end()):
            continue
        prev = text[m.start() - 1] if m.start() > 0 and not mod else ""
        if suffix != "色" and prev and prev not in _COLOR_BARE_PREV:
            continue
        if mod:
            name = f"{mod}{base}色"
            return AttrAsk("color", name, name, specific=True)
        return AttrAsk("color", base, f"{base}色")
    return None


def _parse_size(text: str) -> AttrAsk | None:
    for regex in (_SIZE_LETTER_ASK_RE, _SIZE_MULTI_ASK_RE):
        for m in regex.finditer(text):
            if _negated(text, m.start(), m.end()):
                continue
            if regex is _SIZE_MULTI_ASK_RE and _MODEL_BEFORE_RE.search(text[: m.start()]):
                continue
            v = m.group(1).upper().replace("2XL", "XXL").replace("3XL", "XXXL")
            return AttrAsk("size", v, f"{v}码")
    for m in _SIZE_NUM_ASK_RE.finditer(text):
        n = float(m.group(1))
        if 26 <= n <= 48 and not _negated(text, m.start(), m.end()):
            v = f"{n:g}"
            return AttrAsk("size", v, f"{v}码")
    return None


def _parse_storage(text: str) -> AttrAsk | None:
    for m in _STORAGE_ASK_RE.finditer(text):
        if _negated(text, m.start(), m.end()):
            continue
        v = f"{int(m.group(1))}GB" if m.group(1) else f"{int(m.group(2))}TB"
        return AttrAsk("storage", v, v)
    return None


def _parse_capacity(text: str) -> AttrAsk | None:
    for m in _CAPACITY_ASK_RE.finditer(text):
        if _negated(text, m.start(), m.end()):
            continue
        v = f"{_ml(m.group(1), m.group(2)):g}ml"
        return AttrAsk("capacity", v, v)
    return None


def _parse_screen(text: str) -> AttrAsk | None:
    for m in _SCREEN_ASK_RE.finditer(text):
        if _negated(text, m.start(), m.end()):
            continue
        v = f"{float(m.group(1)):g}"
        return AttrAsk("screen", v, f"{v}英寸")
    return None


def parse_attr_ask(text: str) -> AttrAsk | None:
    """用户文字里问到的第一个 SKU 规格值;没有就返回 None。否定语境("不要黑色的")不算。

    价格数字不会被误认:存储只认 64/128/256/512 G(B) 与 1/2/4 T(B),容量必须带 ml / L 单位,
    尺码必须带"码"(或 XL 这类多字母码),屏幕必须带"英寸 / 寸"。"""
    if not text:
        return None
    for parser in (_parse_storage, _parse_screen, _parse_capacity, _parse_size, _parse_color):
        ask = parser(text)
        if ask is not None:
            return ask
    return None
