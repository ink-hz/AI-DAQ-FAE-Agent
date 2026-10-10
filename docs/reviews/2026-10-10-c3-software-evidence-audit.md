# C3 software evidence implementation audit

C3 adds exact software condition consumption and a private candidate matrix.
No real facts were approved, no roles granted, and no knowledge release activated.
No shared camera runtime files were changed.

The private matrix contains 36 candidate evidence rows across 10 review cases:
25 package-presence rows and 11 document rows. Nine B1 entity review targets have
explicit software-support gaps. Actual source-code, device-test and combination
end-to-end evidence remain absent. Version and platform strings in archive names
were not promoted into compatibility facts. All exact selectors await review.
A1 asset dispositions and B1 ambiguity context are retained alongside K3 evidence.
Link, module and upgrade-operation issues remain separately scoped.

Synthetic regression tests cover both tools, legacy SDK query gaps, exact
selectors, dependency versions, extra operating conditions, role isolation,
combination boundaries, unsupported conflicts and filename inference prevention.
Full deterministic test results are recorded in the implementation handoff.
Real-model evaluation and independent answer-quality acceptance remain Dev gates;
this work used no model calls or production evaluation.

Independent review follow-up found and reproduced two defects: topology-specific
required selectors were not enforced, and matching dependency versions were not
projected into requirement coverage. Synthetic regressions first failed for both
tools. The correction validates topology selectors against request and record
conditions, and maps explicit dependency versions into coverage without allowing
primary-version conflicts. The private matrix is unchanged.
