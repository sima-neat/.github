# Reusable Issue Triage

`reusable-issue-triage.yml` lets product repositories triage issues with Codex
on a private self-hosted runner while keeping GitHub mutations centralized and
allowlisted.

## Caller Workflow

Add this workflow to a product repository:

```yaml
name: Issue Triage

on:
  issues:
    types: [opened, edited, reopened]
  workflow_dispatch:
    inputs:
      issue_number:
        description: Issue number to triage.
        required: true
        type: number
      dry_run:
        description: Propose actions without mutating the issue.
        required: false
        type: boolean
        default: true

permissions:
  contents: read
  issues: write

jobs:
  triage:
    uses: sima-neat/.github/.github/workflows/reusable-issue-triage.yml@main
    with:
      issue_number: ${{ github.event.issue.number || inputs.issue_number }}
      dry_run: ${{ github.event_name == 'workflow_dispatch' && inputs.dry_run || false }}
      repo_triage_path: .github/issue-triage
    secrets: inherit
```

The shared workflow expects a self-hosted runner with labels:

```text
self-hosted, issue-triage
```

That runner must have Codex CLI installed and authenticated for the runner user.

## Repo-Specific Triage Files

Each product repo can optionally add repo-local triage configuration and skills:

```text
.github/issue-triage/
  config.json
  skills/
    repo-triage/
      SKILL.md
  known-issues.md
```

Example `config.json`:

```json
{
  "labels": {
    "allowed": [
      "bug",
      "enhancement",
      "documentation",
      "needs-repro",
      "area:sdk"
    ]
  },
  "automation": {
    "apply_labels": true,
    "post_comment": true
  },
  "cross_reference_repos": [
    {
      "repository": "sima-neat/sdk",
      "ref": "develop",
      "path": "sdk"
    }
  ],
  "max_comment_chars": 1200
}
```

Repo-local files are provided to Codex as context. Codex proposes labels and a
public triage comment, but the shared runner script applies only labels present
in `labels.allowed`.

## Extended Analysis

Codex can request an extended-analysis pass by returning
`extended_analysis_required: true` and listing repositories in
`extended_analysis_repos`. The runner script clones only repositories listed in
`cross_reference_repos`; arbitrary model-requested repositories are ignored.

After the allowed repositories are cloned, Codex runs a second read-only pass
with those repositories mounted through `--add-dir`. Use this for issues that
need cross-repository context, such as a CLI issue that may originate in SDK,
core, model-compiler, or insight behavior.

If any configured cross-reference repo is private, pass a token with read access
through the optional `cross_repo_token` secret:

```yaml
jobs:
  triage:
    uses: sima-neat/.github/.github/workflows/reusable-issue-triage.yml@main
    with:
      issue_number: ${{ github.event.issue.number || inputs.issue_number }}
    secrets:
      cross_repo_token: ${{ secrets.ISSUE_TRIAGE_CROSS_REPO_TOKEN }}
```

## Safety Model

- Codex produces a JSON proposal only.
- The deterministic runner script applies labels/comments.
- Extended-analysis repositories must be explicitly allowlisted in repo config.
- The workflow does not close issues, assign users, set milestones, or edit issue
  bodies.
- If `dry_run` is true, the workflow uploads artifacts but does not mutate the
  issue.
