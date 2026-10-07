"""SKU 规格块 / 离线图片描述块 / BM25 额外字段开关(不加载任何模型)。"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rag.ingest import chunk as chunk_mod
from rag.retrieve import bm25 as bm25_mod

_PRODUCT = {
    "product_id": "p_test_1",
    "title": "测试T恤",
    "brand": "测试牌",
    "category": "服饰运动",
    "sub_category": "T恤",
    "base_price": 99,
    "rag_knowledge": {"marketing_description": "速干面料"},
    "skus": [
        {"properties": {"尺码": "S码", "颜色": "黑色"}, "price": 99},
        {"properties": {"尺码": "M码", "颜色": "黑色"}, "price": 99},
        {"properties": {"尺码": "M码", "颜色": "白色"}, "price": 109},
    ],
}


def _captions(tmp_path, rows):
    p = tmp_path / "caps.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    chunk_mod._load_captions.cache_clear()
    return p


def test_sku_text_dedupes_values_in_order():
    assert chunk_mod.sku_text(_PRODUCT) == "可选规格:尺码:S码、M码;颜色:黑色、白色"
    assert chunk_mod.sku_text({"skus": []}) is None


def test_caption_chunk_from_file(tmp_path, monkeypatch):
    p = _captions(tmp_path, [{"product_id": "p_test_1", "caption": {
        "appearance": "白色短袖", "colors": ["白色"], "materials": [], "style": ["运动"], "visible_text": ["DRY"]}}])
    monkeypatch.setenv("RAG_IMAGE_CAPTIONS_PATH", str(p))
    text = chunk_mod.image_caption_text("p_test_1")
    assert text == "外观:白色短袖;颜色:白色;风格:运动"            # 图中文字默认不进索引
    monkeypatch.setenv("RAG_CAPTION_INCLUDE_TEXT", "1")
    assert chunk_mod.image_caption_text("p_test_1").endswith(";图中文字:DRY")
    monkeypatch.delenv("RAG_CAPTION_INCLUDE_TEXT")
    types = [c.chunk_type for c in chunk_mod.chunks_from_product(_PRODUCT)]
    assert types[-2:] == ["sku", "image_caption"]


def test_flags_turn_new_chunks_off(tmp_path, monkeypatch):
    p = _captions(tmp_path, [{"product_id": "p_test_1", "caption": {"appearance": "白色短袖"}}])
    monkeypatch.setenv("RAG_IMAGE_CAPTIONS_PATH", str(p))
    monkeypatch.setenv("RAG_INDEX_SKU", "0")
    monkeypatch.setenv("RAG_INDEX_IMAGE_CAPTIONS", "0")
    types = [c.chunk_type for c in chunk_mod.chunks_from_product(_PRODUCT)]
    assert "sku" not in types and "image_caption" not in types


def test_missing_caption_file_or_row_yields_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_IMAGE_CAPTIONS_PATH", str(tmp_path / "nope.jsonl"))
    chunk_mod._load_captions.cache_clear()
    assert chunk_mod.image_caption_text("p_test_1") is None
    assert "image_caption" not in [c.chunk_type for c in chunk_mod.chunks_from_product(_PRODUCT)]


def test_bm25_document_unchanged_by_default(monkeypatch):
    monkeypatch.delenv("RAG_BM25_EXTRA_FIELDS", raising=False)
    assert bm25_mod._document_text(_PRODUCT) == "测试T恤 测试牌 服饰运动 T恤 速干面料"


def test_bm25_extra_fields_opt_in(tmp_path, monkeypatch):
    p = _captions(tmp_path, [{"product_id": "p_test_1", "caption": {"appearance": "白色短袖"}}])
    monkeypatch.setenv("RAG_IMAGE_CAPTIONS_PATH", str(p))
    monkeypatch.setenv("RAG_BM25_EXTRA_FIELDS", "sku,caption")
    doc = bm25_mod._document_text(_PRODUCT)
    assert "可选规格:尺码:S码、M码;颜色:黑色、白色" in doc and "外观:白色短袖" in doc
