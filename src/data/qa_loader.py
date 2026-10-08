"""QA jsonl 数据加载层。"""
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class QARecord:
    kb_id: str
    doc_type: str             # selection / compatibility / faq / knowledge / procedure / troubleshooting / concept / operation
    title: str
    question: str
    answer: str
    standard_answer: str
    answer_type: str          # 终结型 / 条件型 / 操作型 / 排查型
    confidence: str           # high / medium
    issue_type: str = ""
    products: list[str] = field(default_factory=list)
    scenario: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    technical_components: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    applicable_channels: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "QARecord":
        return cls(
            kb_id=d["kb_id"],
            doc_type=d["doc_type"],
            title=d["title"],
            question=d["question"],
            answer=d["answer"],
            standard_answer=d["standard_answer"],
            answer_type=d["answer_type"],
            confidence=d["confidence"],
            issue_type=d.get("issue_type", ""),
            products=list(d.get("products", [])),
            scenario=list(d.get("scenario", [])),
            constraints=list(d.get("constraints", [])),
            platforms=list(d.get("platforms", [])),
            technical_components=list(d.get("technical_components", [])),
            keywords=list(d.get("keywords", [])),
            aliases=list(d.get("aliases", [])),
            components=list(d.get("components", [])),
            commands=list(d.get("commands", [])),
            versions=list(d.get("versions", [])),
            applicable_channels=list(d.get("applicable_channels", [])),
            source_refs=list(d.get("source_refs", [])),
        )

    def embed_text(self) -> str:
        """供 embedding 用的文本组合:title + question + aliases + standard_answer + 关键标签。"""
        aliases = "\n".join(self.aliases)
        tags = " ".join(self.scenario + self.products + self.platforms + self.keywords)
        return f"{self.title}\n{self.question}\n{aliases}\n{self.standard_answer}\n标签: {tags}"


def load_qa_jsonl(path: Path) -> list[QARecord]:
    records: list[QARecord] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(QARecord.from_dict(json.loads(line)))
    return records
