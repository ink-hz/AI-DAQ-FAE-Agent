"""解析 产品索引.md / 技术组件索引.md。

格式:
    # 标题
    ## 桶名(产品名 / 技术组件名)
    - [123. 问题标题](path#anchor)

产物:
    {桶名: [IndexEntry(display_id=123, title="问题标题", anchor="#anchor"), ...]}
"""
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IndexEntry:
    display_id: int
    title: str
    anchor: str


_SECTION = re.compile(r"^## (.+)$")
_LINK = re.compile(r"^- \[(\d+)\. (.+?)\]\(.*?(#[^)]+)\)$")


def parse_index_md(path: Path) -> dict[str, list[IndexEntry]]:
    result: dict[str, list[IndexEntry]] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _SECTION.match(line)
        if m:
            current = m.group(1).strip()
            result.setdefault(current, [])
            continue
        if current is None:
            continue
        m = _LINK.match(line.strip())
        if m:
            result[current].append(IndexEntry(
                display_id=int(m.group(1)),
                title=m.group(2).strip(),
                anchor=m.group(3),
            ))
    return result
