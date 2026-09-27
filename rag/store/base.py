"""向量存储层的公共类型与契约。

业务代码(``rag.retrieve.*`` / ``rag.ingest.*``)只依赖本模块定义的
``Doc`` / ``Hit`` 和 ``VectorStore`` 协议,不感知底层是 Chroma 还是 Milvus。

过滤条件的"中立写法"沿用 Chroma 的 where 字典子集(``$and`` / ``$or`` /
``$in`` / ``$nin`` / ``$eq`` / ``$ne`` / ``$gt`` / ``$gte`` / ``$lt`` / ``$lte``),
由各后端自行翻译,见 ``rag.store.filters``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol, Sequence

TEXT_COLLECTION = "products_text"
IMAGE_COLLECTION = "products_image"

# 索引 schema 版本。v2 起每条文档都带 ``brand_country`` 与 ``currency``,
# 过滤条件才可以下推到这两个字段上。入库时把版本号写进索引;
# 查询侧通过 ``VectorStore.filterable_fields()`` 判断能否下推,
# 旧索引(比如线上还没重建的那份)会自动退回到只在 Python 侧过滤。
SCHEMA_VERSION = 2

# v2 文本文档保证具备的标量字段(都可用于过滤)。
TEXT_SCALAR_FIELDS: tuple[str, ...] = (
    "product_id",
    "chunk_type",
    "category",
    "sub_category",
    "brand",
    "brand_country",
    "currency",
    "base_price",
)
# v2 图片向量保证具备的标量字段。
IMAGE_SCALAR_FIELDS: tuple[str, ...] = (
    "product_id",
    "category",
    "sub_category",
    "brand",
    "brand_country",
    "currency",
    "base_price",
)


@dataclass
class Doc:
    id: str
    text: str
    metadata: dict


@dataclass
class Hit:
    id: str
    score: float  # 越大越相似(余弦相似度)
    metadata: dict


class VectorStore(Protocol):
    """所有向量存储后端必须实现的接口。"""

    backend: str

    def upsert_text(self, docs: Sequence[Doc], embeddings: Sequence[Sequence[float]]) -> None: ...

    def query_text(
        self,
        embedding: Sequence[float],
        k: int = 5,
        *,
        where: dict | None = None,
    ) -> list[Hit]: ...

    def text_count(self) -> int: ...

    def upsert_image(
        self,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: Sequence[dict],
    ) -> None: ...

    def query_image(self, embedding: Sequence[float], k: int = 5) -> list[Hit]: ...

    def image_count(self) -> int: ...

    def export(
        self, collection: str, batch_size: int = 1000
    ) -> Iterator[tuple[list[str], list[list[float]], list[dict], list[str | None]]]:
        """分批导出 (ids, 向量, metadata, 文本),用于跨后端迁移而不重新计算向量。"""
        ...

    def reset_collection(self, name: str, properties: dict | None = None) -> None:
        """删除并重建某个集合(用于 ``--rebuild``)。索引可由种子数据完整重建。

        ``properties`` 是随集合一起写入的索引级属性(如 ``origin_fp``),
        只在建集合时写——Chroma 修改已有集合的 metadata 会丢掉 ``hnsw:space``(R15 实测)。
        """
        ...

    def index_properties(self, collection: str = TEXT_COLLECTION) -> dict:
        """建集合时写入的索引级属性(不含后端自己的前缀);集合不存在返回空 dict。"""
        ...

    def filterable_fields(self, collection: str = TEXT_COLLECTION) -> frozenset[str]:
        """该集合在当前索引里**确实存在**的可过滤标量字段。

        查询侧据此决定一个过滤条件能不能下推到数据库。
        """
        ...

    def schema_info(self) -> dict:
        """供 ``/ready`` 和运维脚本展示的索引信息(后端、版本、字段、条数)。"""
        ...
