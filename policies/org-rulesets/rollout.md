# Issue 197 rollout evidence and remaining acceptance

Read-only inspection on November 23, 2026 (America/Vancouver):

- Linux is still private. Its repository settings permit merge, squash and
  rebase; this implementation changes none of those settings.
- The repository inherited-rulesets endpoint reports the shared
  `Protect main, develop, and release branches` ruleset (16775985),
  `Require merge commits into main` (16838264), and the Linux-specific
  two-approval ruleset (27920099), all active. Enterprise restrictions and an
  additional organization deletion restriction are also inherited and are
  outside this change. The rename must retain review ruleset ID 27920099.
- The effective main-branch rules endpoint reports review count 2 for
  27920099, `require_code_owner_review: true`, stale-review dismissal and
  resolved-thread requirements, with `required_reviewers: []`. The separate
  main merge-method layer allows only merge commits. These values establish
  the pre-rollout configuration, not behavioral acceptance.
- `owners-linux` resolves to team ID 20842358. The implementation resolves
  slugs at sync time; this observation is not hardcoded into its payloads.
- The latest four Apply Access Policy runs succeeded at main
  `ef32691248e573b991e46defb216901ae65773b9`. The preceding failure was on
  `451dad7`, before the existing retry/backoff update. The latest successful
  run's logs end with `Access policy apply complete`; another rate-limit fix
  is not justified by this evidence.

Latest inspected success:
https://github.com/sima-neat/.github/actions/runs/45291203223

## Pending before merge and enforcement

- Run the live two-repository, distinct non-admin actor acceptance matrix in
  README.md. Offline tests do not establish merge authorization, direct-push
  denial, stale-review dismissal or isolation enforced by GitHub.
- Record scratch rule IDs, source SHA, actor memberships, PR links, expected
  denials, successful own-team merges, and cleanup. In particular verify that
  `pull_request` bypass works for an update-only rule while separate reviews
  remain mandatory. If it does not, revise the design; do not silently widen
  the bypass to `always`.
- After that acceptance, merge and verify Apply Access Policy creates/grants
  reviewers-linux, then Enforce Org Rulesets updates the existing review ID
  and creates the Linux-only update restriction. Record both successful runs.
- Read inherited/effective Linux rules back and verify two approvals including
  CODEOWNERS, unchanged effective merge methods, and owner/reviewer behavior
  while the repository is still private. Publication remains out of scope.

Keep the implementation PR in draft until scratch acceptance is recorded:
merging it enables the existing push/scheduled enforcement path.
