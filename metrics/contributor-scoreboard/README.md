# Contributor Scoreboard

This module generates a single contributor score from GitHub pull request data.

## Initial scope

The first rollout is limited to the `apps` repository. Scope is configured in
`config.json` and can expand later without changing the scoring code.

## Score formula

Each contributor receives three normalized component scores:

- `delivery`: merged pull request count
- `code`: filtered additions + deletions after path exclusions
- `review`: review submissions and inline review comments on other authors'
  merged pull requests

The final score is a weighted combination of those normalized components:

```text
score = 0.5 * delivery + 0.3 * code + 0.2 * review
```

Weights are configurable in `config.json`.

## Code exclusions

The code component supports glob-based path exclusions to reduce distortion from
generated, vendored, lockfile, and similar churn-heavy files. The initial
pattern list is defined in `config.json`.

## Output

The workflow writes:

- a markdown summary to the workflow summary page
- a JSON artifact with the full ranked contributor list, raw metrics, and
  normalized component scores

## Execution controls

The config also includes:

- `max_pull_requests_per_repo`: hard cap for merged PRs fetched per repository
- `request_workers`: number of parallel workers used for per-PR detail fetches

## Caveats

- This is a heuristic, not a complete measure of impact.
- Review score currently counts review submissions (`APPROVED`,
  `CHANGES_REQUESTED`, `COMMENTED`) and inline review comments.
- Delivery counts merged pull requests, not commit count.
- Bot accounts are excluded automatically, in addition to any explicit login
  exclusions from config.
- Large refactors can still dominate the code component even after exclusions.
