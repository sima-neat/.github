# Repo Default Branch Policy

This policy enforces repository default branch selection across the organization.

## Behavior

For each active repository:

1. If `develop` exists, set default branch to `develop`.
2. Otherwise, if `main` exists, set default branch to `main`.
3. If neither branch exists, skip the repository.

## Config

Edit `config.json`:

- `branch_priority`: ordered list of preferred branch names. The first existing branch is selected.
- `exclude_repos`: list of repository names to skip.
