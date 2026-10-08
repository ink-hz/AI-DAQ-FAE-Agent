"""把 Knowledge/<型号>/*.md 按 H2 切片嵌入 Chroma。"""
import argparse
from pathlib import Path

from src.config import load_config
from src.data.product_loader import ProductChunk, iter_product_chunks
from src.index.chroma_client import (
    embed_texts,
    get_chroma_client,
    get_or_create_collection,
    upsert,
)


def _chunk_to_metadata(c: ProductChunk) -> dict:
    return {
        "model": c.model,
        "md_file": c.md_file,
        "section_title": c.section_title,
    }


def build_product_index(
    *,
    knowledge_dir: Path,
    chroma_dir: Path,
    api_key: str,
    embed_model: str,
    collection_name: str = "product",
) -> int:
    chunks = list(iter_product_chunks(knowledge_dir))
    texts = [c.content for c in chunks]
    embeddings = embed_texts(texts, api_key=api_key, model=embed_model)

    client = get_chroma_client(chroma_dir)
    coll = get_or_create_collection(client, collection_name,
                                    embedding_model=embed_model)

    upsert(
        coll,
        ids=[c.chunk_id for c in chunks],
        embeddings=embeddings,
        documents=texts,
        metadatas=[_chunk_to_metadata(c) for c in chunks],
    )
    return len(chunks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", default="product")
    args = parser.parse_args()
    cfg = load_config()
    n = build_product_index(
        knowledge_dir=cfg.knowledge_dir,
        chroma_dir=cfg.chroma_product_path,
        api_key=cfg.openai_api_key,
        embed_model=cfg.embed_model,
        collection_name=args.collection,
    )
    print(f"Indexed {n} product chunks into {cfg.chroma_product_path}")


if __name__ == "__main__":
    main()
