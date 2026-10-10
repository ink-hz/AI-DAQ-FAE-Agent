# D3 Dev readiness gate

`daq_fae.knowledge.release_readiness` provides an offline static audit and local
explicit release operations. It does not deploy, call models, register reviewers,
or mark answer quality accepted. D4 remains pending even when static readiness
passes. Real DAQ candidates currently fail this gate.

## Trusted inputs

The complete candidate bundle includes snapshot, records, sections/bodies, prior
release ID, release reviewer/date, archive custody/ACL/retrieval references and
source inventory digests, exact runtime identity and contract-test digest, identity
review digest, answerable record IDs, excluded topics/gaps, review packet/decision
digests, update impact obligations, and the frozen Dev question batch and digest.

A detached independent approval binds the canonical SHA-256 of the entire bundle,
excluding only `approval`. The caller supplies
`verify_approval(reviewer, payload_sha256, signature)`. There is no accepting
default or real key registry. A trusted control plane must authenticate that
reviewer and independently check the referenced archive, retrieval, review and
runtime evidence. Hash-shaped strings or operator assertions alone do not prove
custody, original-byte retrieval, semantic validity or reviewer authority. The
aggregate signature is additional release authorization; it never replaces D2
item signatures, C1 content reviews, or fact/access/link contracts.

`audit_readiness` is read only, returns all applicable reason codes, and always
reports `online_eligible=false` and `answer_quality=pending_D4`. It invokes the
same normalized record, section consistency and runtime manifest checks used by
v2 publication. Current link evidence and role restrictions are checked by the
record gate. The exact answerable ID set must match the compiled records.
Software/links require reviewed topic digests or explicit exclusion; excluded
kinds cannot be included. Module projections remain explicitly excluded because
C5 prepares an offline projection and has no online publication integration.

C6 restrictions are mandatory: nonempty Platform Tasks and shared generic trace
HTTP access remain disabled. Operator health/trace evidence used here does not
open those endpoints. Frozen questions must contain every impact-required
question ID. Open impact obligations and unsigned decisions reject staging.

## Immutable staging and explicit transition

`stage_release` audits before writing and uses the existing content-addressed v2
publisher. The manifest embeds the entire signed readiness bundle under
`review.readiness`, making scope, role/status/count summaries, archive identities,
review digests, Dev batch and prior release immutable together. Staging never
changes `active.json`. Public `publish_release` and `activate_release` enforce D3 for every nonempty
package; only truly empty sources, records and sections permit bootstrap without
approval. Empty bootstrap activation takes the same D3 transition lock and only
permits an absent pointer or the exact same empty release ID as an idempotent
operation. It cannot replace any different active release, including a signed
nonempty release or another empty release. Storage/pointer primitives are private and used only by the gate and
explicit offline test fixtures. An arbitrary privately written or rehashed
package still fails the mandatory runtime loader.

`ReviewedKnowledge.load_active` requires an externally supplied trusted verifier
for nonempty content, recomputes the signed manifest binding, and checks the
expected runtime release and upstream SHA. `create_app` supplies its actual
`RUNTIME_RELEASE` and tracked `upstream-source.json` revision and receives the
verifier through an explicit server-side dependency. No environment variable,
manifest field or test-mode flag can supply an accepting verifier. Deployments
without a configured trusted adapter refuse nonempty knowledge at startup; the
existing CLI has no such adapter and refuses nonempty publish/activate/rollback.
Empty `empty-dev-v0` startup remains available. Synthetic app tests create actual
signed D3 bundles and inject a test-owned cryptographic verifier; test-only helpers
are never imported by the application or CLI.

`activate_checked` revalidates the stored approval and all current gates, rebuilds
the expected complete manifest and checks its content hash against the staged ID.
It requires the expected prior ID to equal the current pointer, then explicitly
switches and observes Dev health and trace. Both must report `ai-daq-fae-agent`,
the exact knowledge release and the reviewed runtime release; trace also requires
a trace ID. The observer is a caller-owned trusted adapter; the repository only
contains synthetic observers, not a live Dev connector.

Pointer-write failure (including fsync after replacement) and observation failure
restore the original pointer (or removes the newly created
pointer if no prior release existed). A restore failure propagates as an error;
it is never reported as successful rollback. `rollback_checked` uses only the
active manifest's prior ID, revalidates that release, switches explicitly, and
checks health/trace. Failed rollback observation restores the pre-rollback pointer.
Pointer restoration alone does not prove runtime restoration; operators must
resolve unhealthy observations before claiming service recovery.

All D3 transitions use a local exclusive lock. A trusted parent directory and
exclusive operator ownership are required; private offline fixture primitives do not
participate in that lock and must not be used by operational entrypoints. Cross-process deployment/runtime coordination and an
external transaction spanning pointer plus network observation are out of scope.
Rollback may correctly refuse an expired or no-longer-approved release. No camera
pointer, configuration, index, runtime pin or production state is touched.


## Natural expiry during runtime reload

Publication, activation and rollback still require current link page reviews.
Runtime reload validates immutable approvals and link review integrity while
allowing natural expiry; expired links remain hidden by role views and tools.
The same static validation now applies to v2 section consistency checks, preserving
unrelated facts after a link expires. This exception never accepts missing,
forged, changed, wrongly scoped or invalidly dated link evidence and cannot waive
current-link checks in a public transition.

## Authenticated nonempty access

A valid release signature authorizes the knowledge snapshot, not an anonymous
caller. Nonempty knowledge requires authenticated application mode and the
server-owned subject entitlement map. `create_app` rejects a signed nonempty
release when identity mode is disabled, before any local chat or provider work.
The local empty Dev path never assigns an implicit knowledge role. A genuinely
empty bootstrap continues to work without identity; authenticated entitled users
retain the existing role/revocation/replay contract.
