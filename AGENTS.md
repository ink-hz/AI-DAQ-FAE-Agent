# AI DAQ FAE Agent Project Instructions

Before changing code, prompts, knowledge, evaluation or deployment, read `docs/2026-10-08-数采FAE完整设计.md` and the adjacent AI FAE repository's `AGENTS.md`, `docs/AI_FAE_AGENT_ENGINEERING_PRINCIPLES.md`, `docs/DESIGN_INDEX.md` and `AI_FAE_Agent_工作流设计.md`. The adjacent repository's design branch `docs/data-acquisition-fae-design-20261008` contains the numerical source inventory and original requirements.

- The selected design is a separately versioned DAQ FAE service with independently published knowledge and rollback. Reuse general FAE runtime only at an exact reviewed source revision; do not maintain an untracked copy of its code.
- Before pinning upstream code, verify its production build identity and anchor the selected revision on a protected, durable Git ref. The upstream `EvidencePolicy` hook and reused agent identity/task components are mandatory milestone-0 dependencies; preserve the old FAE default behavior and pass both services' contract tests.
- Treat product/variant identity, combination topology, software compatibility, source conflicts and role visibility as governed evidence. Unreviewed `tmp/` files and attachments are not authoritative knowledge.
- The adjacent FAE `tmp/` snapshot is a local candidate source, not a versioned asset. Keep it Git-ignored and archive its original files with a verified hash manifest in controlled storage before relying on it for a knowledge release.
- Keep source paths out of answer prose. Preserve structured sources, capability/evidence coverage, visible fallback, outcome and trace. Never disguise a configuration or Provider HTTP 400 error as transient 503.
- Use Dev for model replay and evaluation. Require independent Codex or human FAE answer review. Do not run evals against production or let the answering model grade itself.
- Changes to the shared FAE runtime must pass the relevant old FAE quality gates before updating the DAQ pin. DAQ knowledge updates must not silently change the old FAE service.
