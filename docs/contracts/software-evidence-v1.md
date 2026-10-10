# DAQ software evidence contract v1

C3 extends the DAQ tools only. Existing reviewed record, source hash, fact review,
access review and immutable release gates remain authoritative.

## Evidence relations

A software relation binds `entity_id`, `hardware_revision`, `platform`,
`connection_mode`, `software`, `version`, `capability`, and `evidence_level`.
`conditions` binds any additional operating conditions. `software_versions`
explicitly binds dependent Viewer/SDK/firmware versions as an exact dictionary;
`topology_id` identifies a governed combination. Missing dependency data must
never be filled from a package filename, a neighboring record or module evidence.

| Level | Meaning | Positive device support |
| --- | --- | --- |
| `package_present` | Source bytes exist; metadata only | No |
| `documented` | Document states a behavior | No |
| `source_supported` | Reviewed source implementation evidence | No |
| `device_tested` | Reviewed test evidence for one device and exact conditions | Device only |
| `end_to_end_verified` | Reviewed end-to-end test evidence | Exact reviewed scope only |

Levels are separate evidence statements. A fact reviewer asserting either test
level must bind the actual test record as a located source reference. File-only
asset references cannot certify support. Existing publication validation rejects
untested support records; candidate matrices are not publishable K1 records.

## Tool behavior

Both `check_software_support` and `sdk_evidence` require exact entity, hardware
revision, platform, connection, software name/version and capability. SDK keeps
its required `query` argument and adds optional structured selectors; legacy
query-only calls return `software_conditions_required`, with a list of missing
selectors and no source disclosure. Keyword relevance never certifies support.

Every declared operating condition and every required variant selector must be
supplied. Unknown added conditions fail closed. Dependency version dictionaries
must match exactly; absent or different versions return
`software_versions_unconfirmed`. Missing/different topology gives
`software_topology_unconfirmed`. Combination support additionally requires the
end-to-end tier and a visible verified topology with matching entity membership
and platform; a device test gives `end_to_end_test_missing`.

Role filtering precedes all matching. Candidate rows remain invisible. Tools
preserve explicit `unsupported` rows and never manufacture that state from
missing evidence. Contradictory visible supported/unsupported results return a
conflict gap. Lower evidence tiers and file-only evidence return
`device_test_missing` if presented to the matcher. Exact results preserve record
scope, data, evidence tier, release ID and structured source references.

Old records that describe only one software version remain scoped to that
version. They cannot answer a query adding a firmware/SDK/Viewer dependency or a
combination absent from the reviewed record.

## Private candidate preparation

`candidate_matrix(packet)` converts K3 package/firmware/capability cases into
stable candidate relations. Asset metadata and document excerpts become separate
rows; all compatibility selectors stay null until source-specific adjudication.
The question is explicitly a review prompt, never a factual assertion. This
function grants no roles and publishes nothing. B1 identities and ambiguities
are review targets, not inferred device/package mappings.

Real matrices, source references and reproduction inputs remain in a restricted,
Git-ignored workspace. Source paths never belong in answer prose. Publication,
actual device tests, fact approval and permission approval are separate gates.
