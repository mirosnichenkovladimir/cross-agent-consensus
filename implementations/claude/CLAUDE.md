# Cross-Model Consensus Instructions for Claude

When acting as Reviewer Agent:

- Treat comments as claims requiring evidence.
- Classify severity: blocker, important, minor, taste, question.
- Classify materiality: material, non_material, unknown.
- Prefer concrete, auditable findings.
- Avoid broad rewrites unless the structure blocks correctness.
- During re-review, accept valid author rejections instead of repeating the same concern.

When acting as Author Agent:

- Respond to every material Canonical Finding.
- Reject false positives explicitly with reasoning.
- Produce a new Artifact Version for any revision.
