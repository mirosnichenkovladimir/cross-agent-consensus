# Conformance Checklist

An implementation is conforming if it can answer yes to all required checks.

## Required

- [ ] First-round reviewer isolation is preserved.
- [ ] Raw findings are immutable and retained.
- [ ] Every finding references an artifact version.
- [ ] Canonical findings preserve raw finding links.
- [ ] Every material finding receives an author response.
- [ ] Rejections include reasoning.
- [ ] Author revisions create new artifact versions.
- [ ] Required validators are recorded.
- [ ] Consensus is not declared with unresolved material findings.
- [ ] Final report explains terminal outcome.

## Recommended

- [ ] Round limit is explicit.
- [ ] Human escalation reasons are explicit.
- [ ] Non-material findings are retained separately from material findings.
- [ ] Validation evidence can override unsupported model opinion.
- [ ] The run can be resumed from persisted state.
