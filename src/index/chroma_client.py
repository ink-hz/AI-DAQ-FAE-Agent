"""Chroma 客户端薄封装 + OpenAI embedding 调用。"""
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

from src.agent.tracing import current_trace_ctx


def get_chroma_client(persist_dir: Path) -> chromadb.PersistentClient:
    persist_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(persist_dir),
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )


def get_or_create_collection(client: chromadb.PersistentClient, name: str,
                             embedding_model: str | None = None):
    """embedding_model 指纹(20260708):索引与查询嵌入模型必须一致,否则相似度
    是乱数且完全静默。建索引传入模型名写进 collection metadata;已有指纹
    不一致 → 显式报错(禁止混嵌);旧索引无指纹 → 回填。"""
    coll = client.get_or_create_collection(
        name=name, metadata={"hnsw:space": "cosine"})
    if embedding_model:
        existing = (coll.metadata or {}).get("embedding_model")
        if existing is None:
            # modify 不接受 hnsw:* 配置键(距离函数不可变更),只回填指纹键
            merged = {k: v for k, v in (coll.metadata or {}).items()
                      if not str(k).startswith("hnsw:")}
            merged["embedding_model"] = embedding_model
            coll.modify(metadata=merged)
        elif existing != embedding_model:
            raise ValueError(
                f"collection {name!r} 由 embedding 模型 {existing!r} 构建,"
                f"不能用 {embedding_model!r} 混写——请重建索引或改回原模型")
    return coll


def ensure_embedding_model(collection, expected: str) -> None:
    """查询侧校验:指纹存在且不一致 → 显式报错;无指纹(旧索引)迁移期放行。"""
    meta = getattr(collection, "metadata", None)
    fingerprint = meta.get("embedding_model") if isinstance(meta, dict) else None
    if isinstance(fingerprint, str) and fingerprint != expected:
        raise ValueError(
            f"embedding 模型不一致:索引由 {fingerprint!r} 构建,"
            f"查询侧配置为 {expected!r}——相似度将是乱数,请重建索引或对齐配置")


def upsert(
    collection,
    *,
    ids: list[str],
    embeddings: list[list[float]],
    documents: list[str],
    metadatas: list[dict[str, Any]],
) -> None:
    if not (len(ids) == len(embeddings) == len(documents) == len(metadatas)):
        raise ValueError("ids/embeddings/documents/metadatas length mismatch")
    collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)


def query(
    collection,
    *,
    query_embedding: list[float],
    n_results: int = 10,
    where: dict | None = None,
) -> dict:
    ctx = current_trace_ctx()
    if ctx is None:
        return collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
        )
    t0 = time.time()
    with ctx.span("chroma_query", input_summary={}, metadata={
        "n_results": n_results,
        "has_where": where is not None,
    }) as h:
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
        )
        duration_ms = int((time.time() - t0) * 1000)
        result_count = len(result.get("ids", [[]])[0]) if result.get("ids") else 0
        h.set_metadata(duration_ms=duration_ms, result_count=result_count)
        return result


# ---------- OpenAI Embedding ----------

def embed_texts(texts: list[str], *, api_key: str, model: str, batch_size: int = 64) -> list[list[float]]:
    """调 OpenAI embedding API,按 batch 分批,返回顺序与输入一致。"""
    from openai import OpenAI

    ctx = current_trace_ctx()
    total_batches = (len(texts) + batch_size - 1) // batch_size if texts else 0
    span_cm = ctx.span("embedding", input_summary={}, metadata={
        "text_count": len(texts),
        "batch_size": batch_size,
        "embedding_model": model,
        "total_batches": total_batches,
    }) if ctx is not None else nullcontext()

    t0 = time.time()
    with span_cm as h:
        client = OpenAI(api_key=api_key)
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = client.embeddings.create(model=model, input=batch)
            out.extend(d.embedding for d in resp.data)
        if h is not None:
            h.set_metadata(
                duration_ms=int((time.time() - t0) * 1000),
                embeddings_returned=len(out),
            )
        return out
