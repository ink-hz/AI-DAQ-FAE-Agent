# B1 identity and coverage audit

Status: 工程已实现待真实验收. No fact/permission approvals or release were created.

The restricted ignored B1 dictionary indexes 70 unchanged K7 records (64 candidate, 6 conflict, 0 verified), 9 entities, 29 fields, 5 topologies, 6 relations, 23 name entries and 114 existing candidate sections. Source coverage contains the 276 A1 source identities plus two separately hashed external candidate snapshots. Four explicit identity/field ambiguity groups remain pending. All 70 records compare structurally equal to the input, including IDs, statuses, conditions and conflict alternatives.

The entity × field audit has 261 cells: 45 candidate, 6 conflict and 210 `audited_no_source`. The last count means no matching structured record in the supplied inventory; it is not an audit of all original-file content. Role indexes grant zero viewing or forwarding records and remain unassessed. Candidate chapter presence does not fill fact gaps.

Two builds produce identical bytes. Private output directory mode is 0700, every artifact is 0600 and all are Git ignored. Input bundle SHA-256: `839d35d5302f42a05015cccd840c861e019918a6452a63c1943257a6ba19a401`. Dictionary SHA-256: `416c0b536dd61e4b232c8538aa8a005ba7fff9f2026e570cb55705486c28e207`. The bundle binds K7, A1 and A2/A3/A4 indexes by hash; source references are checked against supplied A1/external identities. External snapshot bytes were reread and hashed. The original 276-file archive was not rehashed in B1; A1's archive audit remains its prerequisite.

Validation: 17 synthetic B1 tests pass. Full DAQ suite in the existing Python 3.11 environment: **308 passed, 6 existing deprecation warnings**. The initial full run under system Python 3.9 failed collection due to existing Python 3.11 requirements; it was rerun successfully in the project's compatible environment. `git diff --check` passed.

Independent B1 acceptance, product/SKU and component relationship adjudication, original-content applicability review, field-vocabulary review and viewing/forwarding permission reviews remain open. Inventory verification is not answer-quality acceptance or knowledge publication.
