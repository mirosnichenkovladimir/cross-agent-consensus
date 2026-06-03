# Lifecycle Reference

## Round lifecycle

1. initialize_run
2. author_artifact
3. independent_review
4. normalize_findings
5. optional_conclusion_validation
6. author_response
7. optional_revision
8. rereview
9. evaluate_termination
10. repeat_or_stop

## Canonical finding lifecycle

```text
open
  -> accepted -> fixed -> verified
  -> rejected_by_author -> rejection_accepted
  -> rejected_by_author -> disputed -> escalated
  -> still_valid -> accepted/fixed/escalated
  -> closed_non_material
```

## State meanings

- open: finding is recorded and not yet answered.
- accepted: author agrees it is valid.
- fixed: author claims artifact revision addresses it.
- verified: reviewer or validator confirms it is addressed.
- rejected_by_author: author says the finding is invalid or not applicable.
- rejection_accepted: reviewer/human accepts the rejection.
- still_valid: reviewer says the issue remains.
- disputed: author and reviewer disagree on a material claim.
- escalated: human decision required.
- closed_non_material: finding is explicitly non-material under policy.

## Review focus

Review focus/lens values are optional ReviewBatch metadata for emphasis areas. They do not change reviewer identities or participant selection.

## Conclusion validation

After normalization and before Author Response, the Orchestrator may recall participating reviewers for a `scope_triage` conclusion-validation batch. This is not a fresh review; reviewers validate only listed Canonical Findings and must include rationale for `agree`, `disagree`, or `needs_human`.
