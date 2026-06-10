"""品牌 → 原产国查询表,服务于反选(negation)过滤器。

Round 7 修复:`apply_negation` 此前按品牌字符串字面值和标题/描述关键词
过滤。"不要日系" 没能排除安热沙,因为商品标题里既没有 "日本" 也没有
"日系"。有了这张表,当 AI 生成的种子条目缺少 `provenance.origin_country`
时,过滤器可以通过品牌反查商品的原产国,并在用户排除该国家时
把对应商品剔除。

覆盖范围:当前商品目录里约 30 个具名品牌。新品牌会落到 "unknown"——
行为与 Round 7 之前完全一致(不会引入回归)。
随商品目录扩充逐步扩展此字典即可;Tujie 或 Sam 也可以直接往里加条目,
无需改动 `negation.py`。
"""

from __future__ import annotations

# ISO 3166-1 两位字母国家代码。键统一保持小写并去除首尾空白,便于匹配。
BRAND_ORIGIN: dict[str, str] = {
    # 日系(正是促成本次修复的案例)。
    "安热沙": "JP",
    "anessa": "JP",
    "资生堂": "JP",
    "shiseido": "JP",
    "sk-ii": "JP",
    "sk2": "JP",
    "shu uemura": "JP",
    "kiehl's": "US",
    "kiehls": "US",
    "tatcha": "JP",
    "muji": "JP",
    "kewpie": "JP",
    "pigeon": "JP",
    "gerber": "US",  # 1927 年创立于美国密歇根州 Fremont;2007 年起归雀巢 Nestlé(CH)所有,但品牌原产地按 US 算
    "nintendo": "JP",
    "sony": "JP",
    "索尼": "JP",
    "dji": "CN",
    "calpico": "JP",
    "calpis": "JP",
    "可尔必思": "JP",
    "glico": "JP",
    # 雀巢(Nestlé)系——总部在瑞士沃韦(Vevey)。KitKat 在全球都是雀巢品牌,
    # 唯独美国例外(由 Hershey 持有授权)。反选时按 CH 处理,
    # 这样 "不要瑞士的" 能把它过滤掉,而 "不要日系" 不会误伤。
    "kit kat": "CH",
    "nestlé": "CH",
    "nestle": "CH",
    "uniqlo": "JP",
    # 美系
    "雅诗兰黛": "US",
    "estée lauder": "US",
    "estee lauder": "US",
    "apple": "US",
    "bose": "US",
    "the north face": "US",
    "patagonia": "US",
    "trader joe's": "US",
    "trader joes": "US",
    "levi's": "US",
    "levis": "US",
    "the ordinary": "US",
    "kiehl's since 1851": "US",
    # 法系
    "理肤泉": "FR",
    "la roche-posay": "FR",
    "la roche posay": "FR",
    "兰蔻": "FR",
    "lancôme": "FR",
    "lancome": "FR",
    "巴黎欧莱雅": "FR",
    "l'oréal paris": "FR",
    "loreal paris": "FR",
    "loreal": "FR",
    # 韩系
    "雪花秀": "KR",
    "sulwhasoo": "KR",
    "innisfree": "KR",
    "悦诗风吟": "KR",
    # 德系
    "adidas": "DE",
    "puma": "DE",
    # 英系
    "the body shop": "GB",
    # 意系
    "ferrari": "IT",
    # 国货
    "薇诺娜": "CN",
    "winona": "CN",
    "珀莱雅": "CN",
    "proya": "CN",
    "花西子": "CN",
    "完美日记": "CN",
    "perfect diary": "CN",
    "华为": "CN",
    "huawei": "CN",
    "小米": "CN",
    "xiaomi": "CN",
    "公牛": "CN",
    "bull": "CN",
    "飞鹤": "CN",
    "firmus": "CN",
    "汤臣倍健": "CN",
    "by-health": "CN",
    "斯利安": "CN",
    "海尔": "CN",
    "haier": "CN",
    "迪卡侬": "FR",  # 法国品牌,在国内很流行——为求如实标注,记为 FR
    "decathlon": "FR",
    "凯乐石": "CN",
    "kailas": "CN",
    "牧高笛": "CN",
    "mobigarden": "CN",
    "探路者": "CN",
    "toread": "CN",
    "网易严选": "CN",
    "雅芳婷": "CN",
    "珊珂": "JP",  # 珊珂(Senka)是日本资生堂旗下品牌
    "senka": "JP",
    "百雀羚": "CN",
    "自然堂": "CN",
    "韩束": "CN",
    "丸美": "CN",
    "帮宝适": "US",  # Pampers(美国宝洁 Procter & Gamble 旗下)
    "pampers": "US",
    "好奇": "US",  # Huggies(美国金佰利 Kimberly-Clark 旗下)
    "huggies": "US",
    "嘉宝": "US",  # 即美国 Gerber
    "重庆出版社": "CN",
    "北京十月文艺出版社": "CN",
    "商务印书馆": "CN",
    "吉林美术出版社": "CN",
    # --- Round 7 PM:AI 生成的目录商品在用、但本字典此前缺失的品牌——
    # 通过评测看板上的漏排(leak)审计发现。
    # 数码电子
    "苹果": "US",
    "oppo": "CN",
    "vivo": "CN",
    "联想": "CN",
    # 服饰运动
    "nike": "US",
    "耐克": "US",
    "阿迪达斯": "DE",
    "adidas": "DE",
    "优衣库": "JP",
    "uniqlo": "JP",
    "北面": "US",          # The North Face 的中文名
    "始祖鸟": "CA",        # Arc'teryx
    "arc'teryx": "CA",
    "arcteryx": "CA",
    "萨洛蒙": "FR",        # Salomon
    "salomon": "FR",
    "迈乐": "US",          # Merrell
    "merrell": "US",
    "露露乐蒙": "CA",      # Lululemon
    "lululemon": "CA",
    "hoka": "US",          # 2009 年创立于法国,2013 年起归美国 Deckers 所有
    "osprey": "US",
    "安踏": "CN",
    "李宁": "CN",
    "特步": "CN",
    # 美妆护肤
    "ahc": "KR",
    "玉兰油": "US",        # Olay (P&G)
    "olay": "US",
    "科颜氏": "US",        # Kiehl's
    "kiehl's": "US",
    "kiehls": "US",
    "芳珂": "JP",          # FANCL
    "fancl": "JP",
    "方里": "CN",          # Funny Elves
    # 食品饮料
    "三只松鼠": "CN",
    "三顿半": "CN",
    "东方树叶": "CN",      # 农夫山泉子品牌
    "东鹏": "CN",          # 东鹏特饮
    "伊利": "CN",
    "金典": "CN",          # 伊利子品牌
    "蒙牛": "CN",
    "纯甄": "CN",          # 蒙牛子品牌
    "元气森林": "CN",
    "农夫山泉": "CN",
    "可口可乐": "US",
    "coca-cola": "US",
    "康师傅": "TW",
    "统一": "TW",          # 统一企业
    "日清": "JP",
    "nissin": "JP",
    "李锦记": "CN",
    "lee kum kee": "CN",
    "海天": "CN",
    "百草味": "CN",
    "良品铺子": "CN",
    "红牛": "TH",          # 源自泰国 Krating Daeng
    "雀巢": "CH",          # Nestlé,总部在瑞士
}

# 品牌别名簇。每个集合收录跨语言/不同大小写、但指向同一个品牌的名称。
# 供反选(negation)抽取使用,使 "不要 Nike" 也能命中标注为 "耐克" 的商品,
# 反之亦然。
# 每当某个品牌的中文俗称有对应英文别名(或反过来)、且两种写法都出现在
# 商品目录里时,就新增一个别名簇。
BRAND_ALIASES: tuple[frozenset[str], ...] = (
    frozenset({"nike", "耐克"}),
    frozenset({"apple", "苹果", "iphone", "ipad", "macbook", "airpods"}),
    frozenset({"sony", "索尼"}),
    frozenset({"adidas", "阿迪达斯"}),
    frozenset({"shiseido", "资生堂"}),
    frozenset({"anessa", "安热沙"}),
    frozenset({"sk-ii", "sk2"}),
    frozenset({"the north face", "北面"}),
    frozenset({"merrell", "迈乐"}),
    frozenset({"salomon", "萨洛蒙"}),
    frozenset({"decathlon", "迪卡侬"}),
    frozenset({"arc'teryx", "arcteryx", "始祖鸟"}),
    frozenset({"lululemon", "露露乐蒙"}),
    frozenset({"uniqlo", "优衣库"}),
    frozenset({"olay", "玉兰油"}),
    frozenset({"kiehl's", "kiehls", "科颜氏"}),
    frozenset({"fancl", "芳珂"}),
    frozenset({"l'oréal paris", "巴黎欧莱雅"}),
    frozenset({"la roche-posay", "理肤泉"}),
    frozenset({"lancôme", "兰蔻"}),
    frozenset({"estée lauder", "estee lauder", "雅诗兰黛"}),
    frozenset({"coca-cola", "可口可乐"}),
    frozenset({"nestlé", "nestle", "雀巢"}),
    frozenset({"nissin", "日清"}),
    frozenset({"lee kum kee", "李锦记"}),
    frozenset({"calpico", "calpis", "可尔必思"}),
    frozenset({"sulwhasoo", "雪花秀"}),
    frozenset({"pampers", "帮宝适"}),
    frozenset({"gerber", "嘉宝"}),
    frozenset({"huggies", "好奇"}),
    # R9.A.1 — 跨语言归一化(canonicalization)扩展。收录评委可能用英文或
    # 拼音输入、而商品目录用中文写法(或反过来)的品牌。
    # 每个别名簇必须无歧义——避免一词多义的中文字符串
    # ("博士" 既可指 Bose,也是普通名词)。
    frozenset({"puma", "彪马"}),
    frozenset({"new balance", "newbalance", "nb", "新百伦"}),
    frozenset({"samsung", "三星"}),
    frozenset({"huawei", "华为"}),
    frozenset({"xiaomi", "mi", "小米"}),
    frozenset({"oppo"}),  # OPPO 中英文同名;此条占位保留位置。
    frozenset({"vivo"}),
    frozenset({"asus", "华硕"}),
    frozenset({"dell", "戴尔"}),
    frozenset({"lenovo", "联想"}),
    frozenset({"hp", "惠普"}),
    frozenset({"microsoft", "微软"}),
    frozenset({"google", "谷歌"}),
    frozenset({"dyson", "戴森"}),
    frozenset({"philips", "飞利浦"}),
    frozenset({"bosch", "博世"}),
    frozenset({"casio", "卡西欧"}),
    frozenset({"seiko", "精工"}),
    frozenset({"citizen", "西铁城"}),
    frozenset({"converse", "匡威"}),
    frozenset({"vans", "范斯"}),
    frozenset({"reebok", "锐步"}),
    frozenset({"under armour", "安德玛"}),
    frozenset({"asics", "亚瑟士"}),
    # R13 — 移除了裸写的 "lining":它是常见英文名词(如 "fleece lining"),
    # 即使加了字母边界修复(它是完整单词而非子串,边界检查拦不住),
    # 仍会给英文查询错误地固定 brand_include=['李宁']。
    frozenset({"li-ning", "li ning", "李宁"}),
    frozenset({"anta", "安踏"}),
    frozenset({"xtep", "特步"}),
    frozenset({"erke", "鸿星尔克"}),
    frozenset({"meiji", "明治"}),
    frozenset({"glico", "格力高"}),
    frozenset({"haagen-dazs", "haagen dazs", "哈根达斯"}),
    frozenset({"perrier", "巴黎水"}),
    frozenset({"evian", "依云"}),
)


def expand_brand_aliases(brand: str) -> set[str]:
    """返回某品牌已知的全部别名(均为小写)。若输入不属于任何已知
    别名簇,则只返回 {brand_lower} 本身。

    示例:
        expand_brand_aliases("Nike") → {"nike", "耐克"}
        expand_brand_aliases("某不知名牌") → {"某不知名牌"}
    """
    if not brand:
        return set()
    b = brand.lower().strip()
    for cluster in BRAND_ALIASES:
        if b in cluster:
            return set(cluster)
    return {b}


# 国家代码 → 用户表达排除意图时可能输入的触发关键词。
# 匹配不区分大小写、按子串进行;关键词集合刻意保持精简以保证精确。
COUNTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "JP": ("日本", "日系", "日货", "倭"),
    "US": ("美国", "美系", "美货"),
    "KR": ("韩国", "韩系", "韩货"),
    "FR": ("法国", "法系"),
    "DE": ("德国", "德系"),
    "GB": ("英国", "英系"),
    "IT": ("意大利", "意系"),
    "CN": ("国货", "中国"),  # 很少被排除,但为完整性予以支持
}


def lookup_origin(brand: str) -> str | None:
    """返回品牌对应的 ISO 两位国家代码;未知品牌返回 None。

    匹配不区分大小写并去除首尾空白。双向子串匹配,
    以覆盖 "雅诗兰黛 / Estée Lauder" 这类中英混排标题。
    """
    if not brand:
        return None
    key = brand.strip().lower()
    if key in BRAND_ORIGIN:
        return BRAND_ORIGIN[key]
    # 宽松匹配:任何已知品牌名只要是输入的子串(或反之)即可命中。
    # 这样字典本身可以保持精简。
    for known_brand, country in BRAND_ORIGIN.items():
        if known_brand in key or key in known_brand:
            return country
    return None


def excluded_countries(exclude_keywords: list[str]) -> set[str]:
    """根据反选抽取器给出的 `exclude_keywords`,返回用户想要排除的
    ISO 两位国家代码集合。"""
    excluded: set[str] = set()
    if not exclude_keywords:
        return excluded
    haystack = " ".join(k.lower() for k in exclude_keywords if k)
    for country, kws in COUNTRY_KEYWORDS.items():
        if any(kw in haystack for kw in kws):
            excluded.add(country)
    return excluded


def product_origin(product: dict) -> str | None:
    """解析商品的原产国。优先使用显式的 provenance 元数据
    (Round 6 引入的真实商品带有该字段),否则回退到
    品牌名查表。"""
    prov = product.get("provenance") or {}
    explicit = (prov.get("origin_country") or "").strip().upper()
    if explicit and len(explicit) == 2:
        # 把 AI 生成的演示种子(`source_platform = "AI-gen (demo)"`)视为
        # 原产国未知——其 `origin_country` 默认填 "CN",但我们不希望把
        # 雅诗兰黛(US)的 AI 生成卡片错标成 CN。对 AI 生成条目而言,
        # 品牌名查表更可靠。
        if prov.get("source_platform") == "AI-gen (demo)":
            from_brand = lookup_origin(product.get("brand") or "")
            return from_brand or explicit
        return explicit
    return lookup_origin(product.get("brand") or "")
