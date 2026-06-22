# Release Engineering

The shared Release Engineering workflow provides release automation that can be
called by product repositories before release artifacts are created.

The first supported capability is release issue validation:

1. Find issues in the `Neat Releases` project whose `Target Release` field
   matches the requested release version.
2. Require every matching issue to have `Status = Done`.
3. Stop the release before creating branches, tags, or draft releases when any
   matching issue is not `Done`.
4. Mark matching issues with `Release Status = released` after validation
   passes.
5. Print every touched issue URL in the GitHub Actions log.

## GitHub App

The workflow expects a dedicated GitHub App named
`sima-neat-release-engineering`.

Required app permissions:

- Projects: read/write
- Issues: read
- Metadata: read

Store the app credentials as Actions secrets available to release workflows:

- `NEAT_RELEASES_APP_ID`
- `NEAT_RELEASES_APP_PRIVATE_KEY`

## Calling The Workflow Directly

```yaml
jobs:
  validate-release-readiness:
    uses: sima-neat/.github/.github/workflows/release-engineering.yml@main
    with:
      release_version: ${{ inputs.tag }}
    secrets:
      app_id: ${{ secrets.NEAT_RELEASES_APP_ID }}
      app_private_key: ${{ secrets.NEAT_RELEASES_APP_PRIVATE_KEY }}
```

## Enabling In Vulcan Release

The shared `vulcan-release.yml` workflow has an opt-in validation hook. Product
release workflows should pass `secrets: inherit` and enable validation once the
Release Engineering app secrets are installed.

```yaml
jobs:
  release:
    uses: sima-neat/.github/.github/workflows/vulcan-release.yml@main
    with:
      release_line: ${{ inputs.release_line }}
      tag: ${{ inputs.tag }}
      source_branch: ${{ inputs.source_branch }}
      validate_release_issues: true
    secrets: inherit
```

The validation job runs before the reusable release workflow creates or updates
release branches, tags, or draft GitHub releases.

## Local Dry Run

For local testing with a user token:

```bash
GH_TOKEN="$(gh auth token)" \
python3 scripts/release_issue_gate.py \
  --release-version 0.2.1 \
  --project-owner sima-neat \
  --project-number 6 \
  --dry-run true
```
