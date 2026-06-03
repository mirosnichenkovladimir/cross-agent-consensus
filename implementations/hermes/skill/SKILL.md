---
name: cross-model-consensus
description: Orchestrate auditable author/reviewer consensus loops across heterogeneous agents.
version: 0.1.0
---

# Cross-Model Consensus

Use when the user wants one model/agent to author an artifact and other models/agents to review it until consensus or escalation.

## Process

1. Create run folder.
2. Save task and policy.
3. Run Author Agent to produce Artifact Version.
4. Run Reviewer Agents independently.
5. Preserve raw reviews.
6. Normalize findings.
7. Require Author Response for every material finding.
8. Run re-review.
9. Decide: consensus, next round, escalation, abort.
10. Produce final report.

## Hard rules

- Reviewer findings are claims, not commands.
- First-round reviewers must be isolated.
- Raw findings are immutable.
- Rejected material findings need reasoning and re-review.
- Do not claim consensus while material findings remain unresolved.
