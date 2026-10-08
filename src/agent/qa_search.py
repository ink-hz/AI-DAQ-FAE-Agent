"""QA 检索 — D 桶第 1 步(工作流设计 §6.4)。

纯检索:Chroma where 过滤 + 向量召回 + top-K 排序。
**不做决策**(direct_reuse / synthesize / fallback 由 retrieval_quality.evaluate 决定)。

build_where_clause / _distance_to_sim 同时被 engine_a.py 复用,直到 T9 删掉 engine_a。
"""
from src.agent.schema import RequestSchema, RetrievalHit
from src.index.chroma_client import embed_texts, ensure_embedding_model

TOP_K = 5


def _list_field_filter(field: str, values: list[str]) -> list[dict]:
    """对 list 字段,Chroma 不能直接 $in,改用多个 $contains $or。"""
    if not values:
        return []
    if len(values) == 1:
        return [{field: {"$contains": f"|{values[0]}|"}}]
    return [{"$or": [{field: {"$contains": f"|{v}|"}} for v in values]}]


def build_where_clause(schema: RequestSchema) -> dict | None:
    clauses: list[dict] = []
    clauses.extend(_list_field_filter("products", schema.products))
    clauses.extend(_list_field_filter("scenario", schema.scenario))
    clauses.extend(_list_field_filter("platforms", schema.platforms))
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _distance_to_sim(d: float) -> float:
    """Chroma cosine distance ∈ [0, 2] → sim ∈ [-1, 1],近似当 1 - d,clamp 到 [0, 1]。"""
    return max(0.0, 1.0 - d)


def qa_search(
    *,
    schema: RequestSchema,
    user_query: str,
    collection,
    api_key: str,
    embed_model: str,
    top_k: int = TOP_K,
) -> list[RetrievalHit]:
    """纯检索 pipeline:where 过滤 + embed + Chroma query → top-K hits(sim 降序)。

    返回空 list 表示无候选(调用方 fae_experience.handle 判 no_strong_signal)。
    """
    where = build_where_clause(schema)
    ensure_embedding_model(collection, embed_model)
    embedding = embed_texts([user_query], api_key=api_key, model=embed_model)[0]
    raw = collection.query(
        query_embeddings=[embedding],
        n_results=top_k,
        where=where,
    )

    hits: list[RetrievalHit] = []
    if raw["ids"] and raw["ids"][0]:
        for i, kb_id in enumerate(raw["ids"][0]):
            sim = _distance_to_sim(raw["distances"][0][i])
            meta = raw["metadatas"][0][i]
            hits.append(RetrievalHit(
                source="qa",
                kb_id=kb_id,
                title=meta.get("title", ""),
                snippet=raw["documents"][0][i],
                score=sim,
                metadata=meta,
            ))
    return hits
