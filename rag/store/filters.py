"""过滤条件的中立写法 → 各后端方言。

中立写法是 Chroma where 字典的一个受限子集,``rag.retrieve.query._build_where``
产出的就是它:

    {"category": "美妆护肤"}                          # 等值简写
    {"brand": {"$nin": ["资生堂", "花王"]}}
    {"$and": [{...}, {"$or": [{...}, {...}]}]}

Chroma 直接吃这个字典;Milvus 需要布尔表达式字符串,由 ``to_milvus_expr``
翻译。不在子集里的写法一律抛 ``UnsupportedFilter``——宁可在测试里炸,
也不要在线上悄悄变成"不过滤"。
"""

from __future__ import annotations

import json
import re
from typing import Any

_COMPARE_OPS = {
    "$eq": "==",
    "$ne": "!=",
    "$gt": ">",
    "$gte": ">=",
    "$lt": "<",
    "$lte": "<=",
}
_LIST_OPS = {"$in": "in", "$nin": "not in"}
_LOGICAL_OPS = {"$and": "and", "$or": "or"}
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsupportedFilter(ValueError):
    """过滤条件超出了中立子集,或结构不合法。"""


def fields_in_where(where: dict | None) -> set[str]:
    """返回过滤条件里引用到的所有字段名。"""
    out: set[str] = set()

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for key, val in node.items():
            if key in _LOGICAL_OPS:
                for child in val or []:
                    walk(child)
            elif not key.startswith("$"):
                out.add(key)

    walk(where)
    return out


def validate_where(where: dict | None) -> None:
    """校验过滤条件在中立子集之内;不合法则抛 ``UnsupportedFilter``。"""
    if where:
        to_milvus_expr(where)


def to_milvus_expr(where: dict | None) -> str:
    """把中立过滤条件翻译成 Milvus 布尔表达式。空条件返回空字符串。"""
    if not where:
        return ""
    return _expr(where)


def _literal(value: Any) -> str:
    # bool 要先于 int 判断(bool 是 int 的子类)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise UnsupportedFilter(f"non-finite number in filter: {value!r}")
        return repr(value)
    if isinstance(value, str):
        # JSON 字符串字面量:双引号 + 反斜杠转义,中文原样保留
        return json.dumps(value, ensure_ascii=False)
    raise UnsupportedFilter(f"unsupported literal type {type(value).__name__}: {value!r}")


def _field(name: str) -> str:
    if not _FIELD_RE.match(name):
        raise UnsupportedFilter(f"invalid field name: {name!r}")
    return name


def _expr(node: Any) -> str:
    if not isinstance(node, dict) or not node:
        raise UnsupportedFilter(f"filter node must be a non-empty dict, got {node!r}")

    # 同一层多个键视为隐式 AND(Chroma 要求显式 $and,这里宽松处理)
    if len(node) > 1:
        return " and ".join(f"({_expr({k: v})})" for k, v in node.items())

    ((key, val),) = node.items()

    if key in _LOGICAL_OPS:
        if not isinstance(val, list) or not val:
            raise UnsupportedFilter(f"{key} expects a non-empty list")
        parts = [_expr(child) for child in val]
        if len(parts) == 1:
            return parts[0]
        return f" {_LOGICAL_OPS[key]} ".join(f"({p})" for p in parts)

    if key.startswith("$"):
        raise UnsupportedFilter(f"unsupported operator at top level: {key}")

    field = _field(key)
    if not isinstance(val, dict):
        return f"{field} == {_literal(val)}"

    if len(val) != 1:
        raise UnsupportedFilter(f"field {field!r} must have exactly one operator, got {list(val)}")
    ((op, operand),) = val.items()

    if op in _LIST_OPS:
        if not isinstance(operand, (list, tuple)) or not operand:
            raise UnsupportedFilter(f"{op} on {field!r} expects a non-empty list")
        items = ", ".join(_literal(x) for x in operand)
        return f"{field} {_LIST_OPS[op]} [{items}]"
    if op in _COMPARE_OPS:
        return f"{field} {_COMPARE_OPS[op]} {_literal(operand)}"
    raise UnsupportedFilter(f"unsupported operator {op!r} on field {field!r}")
