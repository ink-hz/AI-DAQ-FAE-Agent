"""型号 markdown 切片 + 目录索引。

目录形态:
    Knowledge/<型号>/index.md product.md hardware.md software.md
    Knowledge/<型号>/source_<分类>_<文件名>.*    资产,不在切片范围

切片粒度:按二级标题(## XXX)。每个切片携带 model/md_file/section_title 元信息。
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

KNOWLEDGE_MD_NAMES = {"index.md", "product.md", "hardware.md", "software.md", "knowledge.md"}


@dataclass(frozen=True)
class ProductChunk:
    model: str            # Gemini_335L
    md_file: str          # 相对型号目录的文件名,如 hardware.md
    section_title: str    # ## 后面的标题
    content: str          # 包含 H2 标题的完整段(到下一个 H2 之前)

    @property
    def chunk_id(self) -> str:
        safe_section = re.sub(r"\s+", "_", self.section_title)
        return f"{self.model}::{self.md_file}::{safe_section}"


def _is_model_dir(p: Path) -> bool:
    if not p.is_dir() or p.name.startswith("_"):
        return False
    md_present = any((p / n).exists() for n in KNOWLEDGE_MD_NAMES)
    return md_present


def load_product_catalog(knowledge_dir: Path) -> dict[str, dict]:
    """返回 {型号: {"dir": str, "md_files": [str, ...]}}。"""
    catalog: dict[str, dict] = {}
    for child in sorted(knowledge_dir.iterdir()):
        if not _is_model_dir(child):
            continue
        md_files = sorted(
            f.name for f in child.iterdir()
            if f.is_file() and f.name in KNOWLEDGE_MD_NAMES
        )
        catalog[child.name] = {
            "dir": str(child),
            "md_files": md_files,
        }
    return catalog


_H2 = re.compile(r"^## (.+)$", re.MULTILINE)


def _split_by_h2(text: str) -> list[tuple[str, str]]:
    """返回 [(section_title, section_content_including_h2_line), ...]。"""
    matches = list(_H2.finditer(text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((m.group(1).strip(), text[m.start():end].strip()))
    return out


def iter_product_chunks(knowledge_dir: Path) -> Iterator[ProductChunk]:
    for model_name, info in load_product_catalog(knowledge_dir).items():
        for md_name in info["md_files"]:
            md_path = Path(info["dir"]) / md_name
            text = md_path.read_text(encoding="utf-8")
            for title, content in _split_by_h2(text):
                yield ProductChunk(
                    model=model_name,
                    md_file=md_name,
                    section_title=title,
                    content=content,
                )
