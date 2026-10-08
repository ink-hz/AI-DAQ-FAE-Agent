from __future__ import annotations

from typing import Any

from src.data.qa_loader import QARecord


def qa_record_to_review_payload(record: QARecord) -> dict[str, Any]:
    technical_tags = record.technical_components or record.components
    return {
        "source_type": "knowledge_qa",
        "source_ref": record.kb_id,
        "turn_id": None,
        "question": record.question,
        "original_answer": record.answer,
        "reviewed_answer": record.standard_answer,
        "product_tags": record.products,
        "scenario_tags": record.scenario,
        "technical_tags": technical_tags,
        "sdk_tags": record.platforms,
        "quality_score": _confidence_to_score(record.confidence),
        "review_status": "pending",
        "reviewer": "knowledge-import",
        "review_notes": "imported from Knowledge_QA",
        "should_export_to_knowledge": False,
        "metadata": {
            "doc_type": record.doc_type,
            "answer_type": record.answer_type,
            "issue_type": record.issue_type,
            "confidence": record.confidence,
            "constraints": record.constraints,
            "keywords": record.keywords,
            "aliases": record.aliases,
            "commands": record.commands,
            "versions": record.versions,
            "applicable_channels": record.applicable_channels,
            "source_refs": record.source_refs,
        },
    }


def _confidence_to_score(confidence: str) -> int:
    return 5 if confidence == "high" else 4
