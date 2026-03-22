# Repo Environments Policy

Use `config.json` to define standard deployment environments that should exist across repositories.

## Behavior

- The sync script creates or updates each configured environment for each managed repository.
- By default, only public repositories are targeted (`public_repos_only: true`).
- Excluded repositories are skipped via `exclude_repos`.

## Standard environments

This policy currently standardizes:

- `PyPi`
- `CloudFlare-R2`

`CloudFlare-R2` is configured with:

- `protected_branches: false`
- `custom_branch_policies: false`

This means deployments are allowed from all branches and tags.

## PyPi restriction

`PyPi` is configured with custom deployment policies and only allows deployments from tags matching:

- `v*`
