# Contributor Scoreboard

This module generates a single contributor score from GitHub pull request data.

## Current scope

The current rollout uses all-time data from the public repositories:

- `apps`
- `elxr-sdk`
- `insight`

The Pages site renders four scoreboards on one page:

- `All Repos`
- `apps`
- `elxr-sdk`
- `insight`

Scope is configured in `config.json`.

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
- a JSON artifact with the configured scoreboards, raw metrics, and normalized
  component scores
- a GitHub Pages site that renders the latest scoreboard from `scoreboard.json`

## Execution controls

The config also includes:

- `max_pull_requests_per_repo`: hard cap for merged PRs fetched per repository
- `request_workers`: number of parallel workers used for per-PR detail fetches

## Config shape

The config defines:

- `scoreboards`: named scoreboard sections and the repo list for each section
- `window_days`: `null` for all-time data, or a numeric rolling window
- weights, exclusions, and fetch controls

## Pages publication

The site template lives in `metrics/contributor-scoreboard/site/index.html`.
The workflow copies that static file plus the generated `scoreboard.json` into a
Pages artifact and deploys it with GitHub Pages.

The repository still needs a one-time GitHub Pages setup that uses GitHub
Actions as the publishing source.

## Caveats

- This is a heuristic, not a complete measure of impact.
- Review score currently counts review submissions (`APPROVED`,
  `CHANGES_REQUESTED`, `COMMENTED`) and inline review comments.
- Delivery counts merged pull requests, not commit count.
- The same raw repo metrics are reused across all configured scoreboard
  sections, including the aggregate board.
- Bot accounts are excluded automatically, in addition to any explicit login
  exclusions from config.
- Large refactors can still dominate the code component even after exclusions.
