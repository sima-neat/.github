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

Each is configured with deployment branch policy:

- `protected_branches: true`
- `custom_branch_policies: false`

This means deployments are allowed only from protected branches.
