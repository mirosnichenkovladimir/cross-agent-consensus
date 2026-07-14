# Cross-Model Consensus Instructions for Codex

When participating in a consensus run, follow the role assigned by the Orchestrator.

## If Author Agent

- Produce the requested Artifact Version.
- Respond explicitly to every material Normalized Finding.
- Valid responses: accept, reject, partially_accept, request_clarification.
- If rejecting, explain why the finding is invalid, non-material, or outside scope.
- Do not silently apply all reviewer suggestions.
- If revising, identify the new Artifact Version.

## If Reviewer Agent

- Review the provided Artifact Version only.
- In first-round review, do not depend on other reviewers' findings.
- Emit structured findings with severity, confidence, materiality, location, claim, evidence, and suggested fix.
- Distinguish blockers from taste.
- During re-review, decide whether the author fix/rejection is valid.
