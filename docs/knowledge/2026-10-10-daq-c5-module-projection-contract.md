# C5 read-only module projection contract

Status: 工程已实现待真实验收. This is an offline preparation contract, with no runtime tool registration or release-loader integration.

`read_git_source` reads bytes from an explicit annotated or lightweight tag and exact commit, verifies their equality, and records repository identity, ref, revision, source path and SHA-256. It never reads the working-tree version or writes to the camera repository. A tag does not prove remote protection, deployment identity, or source applicability. The camera maintainer must select and attest a durable version before republication. This task does not update the shared runtime pin.

`project_module_facts` accepts a manually prepared envelope. The relation identifies the exact host device and revision and exact module and revision, with source references and verified status. Every fact retains its own ID, module identity/revision, field, value, unit, conditions, source references and verified status. Camera status labels are not automatically mapped to DAQ approval. A fact envelope must be reviewed against its original byte snapshot; this function does not automatically interpret camera YAML or verify source semantics.

Four separate named, dated approval records bind the same canonical SHA-256 of relation, source, complete facts, viewing roles and forwarding roles: relation adjudication, camera source selection, republication, and permission. Any value, revision, source or role change invalidates them. These records belong to the trusted offline review process, not caller-supplied authentication; the caller must authenticate adjudicators and bind decisions to retained originals. A reviewer assignment alone is not approval.

The gate checks permission before exposing any source, relationship or fact. Unknown roles receive no evidence. Forwarding rights never exceed viewing rights. Pending, conflicting or mismatched facts fail closed. Output is a deep copy and preserves the full projection audit only for authorized reviewers. Source paths occur solely in structured audit data, never generated answer prose.

Only `requested_scope=module` can yield facts. Every returned fact remains `scope=module`, and `device_claims=[]` is unconditional. Module precision, synchronization, SDK, power or recording information supplies no host-device or topology claim. Whole-device requests require separate whole-device evidence. Approved synthetic envelopes still have `online_eligible=false`: passing this preparation gate never publishes knowledge or changes an online view.

For the real EG-DB / Gemini 335L case, the B1 relation remains candidate with unconfirmed module revision. Exact relationship adjudication by 苍渊, camera-maintainer source selection and durability attestation, republication review and permission review are pending. Private candidate snapshots remain ignored and restricted; no real module facts are projected.
