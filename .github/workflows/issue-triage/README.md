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
  "max_comment_chars": 1200
}
```

Repo-local files are provided to Codex as context. Codex proposes labels and a
public triage comment, but the shared runner script applies only labels present
in `labels.allowed`.

## Safety Model

- Codex produces a JSON proposal only.
- The deterministic runner script applies labels/comments.
- The workflow does not close issues, assign users, set milestones, or edit issue
  bodies.
- If `dry_run` is true, the workflow uploads artifacts but does not mutate the
  issue.
