"""Agent 内部数据契约。

RequestSchema  — 从用户问题里抽出的需求字段,推理引擎的输入
RetrievalHit   — 检索引擎(A/B)输出的一条候选
AgentResponse  — 最终返回给前端的结构
"""
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Bucket:Agent 的 5 个路由分类(枚举见工作流设计 §1.1,每轮 LLM 自评分到 5 桶)。
Bucket = Literal[
    "catalog_overview",
    "spec_or_compat",
    "selection",
    "fae_experience",
    "out_of_scope",
]
AttachmentDependency = Literal[
    "required_for_answer",
    "supplemental",
    "unknown",
]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = str(raw).strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


class ProfileMinimumRelationRequirement(BaseModel):
    """Same-profile lower bounds that one alternative must satisfy together."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    alternative: str = Field(min_length=1)
    stream: Literal["depth", "rgb"]
    min_pixels: int | None = Field(default=None, gt=0)
    min_width: int | None = Field(default=None, gt=0)
    min_height: int | None = Field(default=None, gt=0)
    min_fps: int | None = Field(default=None, gt=0)
    format: str | None = None

    @model_validator(mode="after")
    def _requires_geometry(self) -> Self:
        if self.min_pixels is None and not (
            self.min_width is not None and self.min_height is not None
        ):
            raise ValueError(
                "profile minimum requires min_pixels or min_width + min_height")
        return self


class AssessmentRelationRequirement(BaseModel):
    """Stable identity for a relation branch that needs real-world assessment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_\-]*$")
    alternative: str = Field(min_length=1)


class ConstraintRelation(BaseModel):
    """One explicit, non-recursive logical relation preserved from user intent.

    This is deliberately not a fact constraint AST: field/op/value mapping remains
    the evidence loop's responsibility.  The relation only prevents an explicit OR
    from being flattened into an AND-list before evidence collection.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_\-]*$")
    relation: Literal["any_of"] = "any_of"
    alternatives: list[str] = Field(min_length=2)
    # Strings are accepted only for persisted/legacy traces. New schema output
    # uses AssessmentRelationRequirement so the evidence loop can match by ID.
    assessment_required: list[str | AssessmentRelationRequirement] = Field(
        default_factory=list)
    profile_minimums: list[ProfileMinimumRelationRequirement] = Field(
        default_factory=list)

    @field_validator("alternatives")
    @classmethod
    def _normalize_alternatives(cls, values: list[str]) -> list[str]:
        normalized = _dedupe(values)
        if len(normalized) < 2:
            raise ValueError("any_of requires at least two distinct alternatives")
        return normalized

    @field_validator("assessment_required")
    @classmethod
    def _normalize_assessment_required(
        cls, values: list[str | AssessmentRelationRequirement],
    ) -> list[str | AssessmentRelationRequirement]:
        result: list[str | AssessmentRelationRequirement] = []
        seen: set[str] = set()
        for value in values:
            if isinstance(value, str):
                normalized: str | AssessmentRelationRequirement = value.strip()
                key = "text:" + normalized.lower()
                if not normalized:
                    continue
            else:
                normalized = value
                key = "id:" + value.id.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(normalized)
        return result

    @model_validator(mode="after")
    def _assessment_must_reference_alternative(self) -> Self:
        unknown = [
            item.alternative if isinstance(item, AssessmentRelationRequirement) else item
            for item in self.assessment_required
            if (
                item.alternative
                if isinstance(item, AssessmentRelationRequirement)
                else item
            ) not in self.alternatives
        ]
        if unknown:
            raise ValueError(
                "assessment_required must be a subset of alternatives")
        unknown_profiles = [
            item.alternative for item in self.profile_minimums
            if item.alternative not in self.alternatives
        ]
        if unknown_profiles:
            raise ValueError(
                "profile_minimums alternatives must be a subset of alternatives")
        return self


def _merge_constraint_relations(
    old: list[ConstraintRelation], new: list[ConstraintRelation], *, reset: bool,
) -> list[ConstraintRelation]:
    """Carry relations across follow-ups; a repeated ID is replaced by newest."""
    merged = [] if reset else list(old)
    positions = {item.id: index for index, item in enumerate(merged)}
    for item in new:
        if item.id in positions:
            merged[positions[item.id]] = item
        else:
            positions[item.id] = len(merged)
            merged.append(item)
    return merged


class RequestSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Bucket
    # §2.1.1 LLM 自评置信度;default high — 字段缺失时不强行走 fae_experience 兜底
    intent_confidence: Literal["high", "low"] = "high"
    # confidence=low 时 LLM 填的备选桶,按可能性排序最多 2 个
    intent_alternatives: list[Bucket] = Field(default_factory=list)

    scenario: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    constraint_relations: list[ConstraintRelation] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    technical_components: list[str] = Field(default_factory=list)
    channel: Literal["fae", "ecom"] = "fae"
    # 本轮用户问题对新附图片的依赖程度。它是逐轮判断，不能从历史 carry。
    attachment_dependency: AttachmentDependency = "unknown"

    # §2.5 每桶都可能填(B 桶填 products,C 桶填 scenario/constraints/platforms/防护)
    missing_for_bucket: list[str] = Field(default_factory=list)
    # §2.4 schema extractor 自评:新问题与上轮 schema 全不相交 → True,orchestrator 据此重置 session
    is_topic_switch: bool = False

    def merge(self, other: Self) -> "RequestSchema":
        """跨轮累积 — 见工作流设计 §2.4。

        - topic switch: replace every active field; do not carry prior context
        - scenario / constraints / platforms / technical_components: carry + append
          (LLM 在 prompt 中去重,不在此 dedupe)
        - products: 本轮非空覆盖上轮;本轮空且不是 topic switch 时保留旧值
          (用户常用"那这个在机械臂上能用吗"承接上一轮型号)
        - intent / intent_confidence / intent_alternatives / missing_for_bucket /
          is_topic_switch / channel / attachment_dependency: 总是用新值
          (每轮重判,不 carry)
        """
        if other.is_topic_switch:
            return RequestSchema(
                intent=other.intent,
                intent_confidence=other.intent_confidence,
                intent_alternatives=list(other.intent_alternatives),
                channel=other.channel,
                scenario=_dedupe(other.scenario),
                constraints=_dedupe(other.constraints),
                constraint_relations=_merge_constraint_relations(
                    [], other.constraint_relations, reset=True,
                ),
                products=_dedupe(other.products),
                platforms=_dedupe(other.platforms),
                technical_components=_dedupe(other.technical_components),
                attachment_dependency=other.attachment_dependency,
                missing_for_bucket=list(other.missing_for_bucket),
                is_topic_switch=True,
            )

        return RequestSchema(
            intent=other.intent,
            intent_confidence=other.intent_confidence,
            intent_alternatives=list(other.intent_alternatives),
            channel=other.channel,
            scenario=_dedupe(self.scenario + other.scenario),
            constraints=_dedupe(self.constraints + other.constraints),
            constraint_relations=_merge_constraint_relations(
                self.constraint_relations,
                other.constraint_relations,
                reset=False,
            ),
            products=list(other.products) if other.products else list(self.products),
            platforms=_dedupe(self.platforms + other.platforms),
            technical_components=_dedupe(self.technical_components + other.technical_components),
            attachment_dependency=other.attachment_dependency,
            missing_for_bucket=list(other.missing_for_bucket),
            is_topic_switch=other.is_topic_switch,
        )


class RetrievalHit(BaseModel):
    source: Literal["qa", "product"]
    kb_id: str | None = None      # QA: kb_id;Product: chunk_id
    title: str
    snippet: str                  # 答案/段落文本(用于喂 LLM)
    score: float
    metadata: dict = Field(default_factory=dict)


class AgentResponse(BaseModel):
    text: str
    sources: list[dict] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    # §2.6 done.template:concise / comparison / checklist / refusal / clarification
    # (procedure 已退役,合并到 checklist;旧 D 桶 answer_type=操作型 prompt 内部仍生成步骤式文本)
    template: Literal["concise", "comparison", "checklist", "refusal", "clarification"] = "concise"
