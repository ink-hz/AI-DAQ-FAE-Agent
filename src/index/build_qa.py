"""把 Knowledge_QA jsonl 嵌入 Chroma collection。

Metadata 设计:
    - 标量字段直接存(kb_id, doc_type, answer_type, confidence, issue_type, title)
    - 列表字段:Chroma 不支持 list metadata,改为 "|item1|item2|" 分隔串,
      过滤时用 $contains 语法。

运行:
    python -m src.index.build_qa
"""
import argparse
from pathlib import Path

from src.config import load_config
from src.data.qa_loader import QARecord, load_qa_jsonl
from src.index.chroma_client import (
    embed_texts,
    get_chroma_client,
    get_or_create_collection,
    upsert,
)


def _list_to_str(xs: list[str]) -> str:
    """空列表 → "";非空 → "|a|b|c|"。"""
    return "|" + "|".join(xs) + "|" if xs else ""


def _qa_to_metadata(rec: QARecord) -> dict:
    return {
        "kb_id": rec.kb_id,
        "doc_type": rec.doc_type,
        "answer_type": rec.answer_type,
        "confidence": rec.confidence,
        "title": rec.title,
        "issue_type": rec.issue_type,
        "products": _list_to_str(rec.products),
        "scenario": _list_to_str(rec.scenario),
        "platforms": _list_to_str(rec.platforms),
        "technical_components": _list_to_str(rec.technical_components),
        "applicable_channels": _list_to_str(rec.applicable_channels),
        "aliases": _list_to_str(rec.aliases),
    }


def build_qa_index(
    *,
    qa_jsonl_path: Path,
    chroma_dir: Path,
    api_key: str,
    embed_model: str,
    collection_name: str = "qa_kb",
) -> int:
    records = load_qa_jsonl(qa_jsonl_path)
    texts = [r.embed_text() for r in records]
    embeddings = embed_texts(texts, api_key=api_key, model=embed_model)

    client = get_chroma_client(chroma_dir)
    coll = get_or_create_collection(client, collection_name,
                                    embedding_model=embed_model)

    upsert(
        coll,
        ids=[r.kb_id for r in records],
        embeddings=embeddings,
        documents=[r.standard_answer for r in records],
        metadatas=[_qa_to_metadata(r) for r in records],
    )
    return len(records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", default="qa_kb")
    args = parser.parse_args()
    cfg = load_config()
    n = build_qa_index(
        qa_jsonl_path=cfg.knowledge_qa_dir / "AI_FAE_高质量知识库.jsonl",
        chroma_dir=cfg.chroma_qa_path,
        api_key=cfg.openai_api_key,
        embed_model=cfg.embed_model,
        collection_name=args.collection,
    )
    print(f"Indexed {n} QA records into {cfg.chroma_qa_path}")


if __name__ == "__main__":
    main()
