"""4 种输出模板。

LLM 主推理已经按 prompt 给出文本主体,这里只负责加上格式骨架(风险提示、追问、表格、序号)。
"""
import re

_QA_TAG = re.compile(r"\[FAE-([A-Za-z0-9_-]+)\]")
_FILE_TAG = re.compile(r"\[([\w/]+)/(\w+\.md)(?::(\d+))?\]")


def render_concise(*, body: str, risk_notes: list[str], follow_ups: list[str]) -> str:
    parts = [body.strip()]
    for r in risk_notes:
        parts.append(f"\n⚠️ {r}")
    if follow_ups:
        questions = "\n".join(f"- {q}" for q in follow_ups[:2])
        parts.append(f"\n\n**还需了解**:\n{questions}")
    return "".join(parts)


def render_comparison(rows: list[dict]) -> str:
    if not rows:
        return ""
    headers = list(rows[0].keys())
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(h, "")) for h in headers) + " |")
    return "\n".join(lines)


def render_procedure(*, steps: list[str], warnings: list[str]) -> str:
    parts = [f"{i + 1}. {s}" for i, s in enumerate(steps)]
    for w in warnings:
        parts.append(f"\n⚠️ {w}")
    return "\n".join(parts)


def render_clarification(questions: list[str]) -> str:
    qs = questions[:2]
    body = "为了精准推荐,请告诉我:\n"
    for i, q in enumerate(qs):
        body += f"{['①', '②'][i]} {q}\n"
    return body


def extract_source_tags(text: str) -> list[dict]:
    tags: list[dict] = []
    for m in _QA_TAG.finditer(text):
        tags.append({"type": "qa", "id": f"FAE-{m.group(1)}", "raw": m.group(0)})
    for m in _FILE_TAG.finditer(text):
        dir_part = m.group(1)
        tags.append({
            "type": "product",
            "model": dir_part.split("/")[-1],
            "path": f"{dir_part}/{m.group(2)}",
            "line": int(m.group(3)) if m.group(3) else None,
            "raw": m.group(0),
        })
    return tags
