# Repo Merge Method Policy

This policy enforces repository pull request merge-method settings across the
organization.

## Behavior

For each targeted repository, the sync script patches the repository settings
with the configured merge-method flags.

The initial intended use is to enforce squash-only merges for a starter set of
repositories without relying on manual GitHub dashboard changes.

## Config

Edit `config.json`:

- `target_repos`: list of repository names to manage explicitly. When omitted or
  empty, the policy applies to all active repositories in the org except those
  in `exclude_repos`.
- `exclude_repos`: list of repository names to skip.
- `defaults`: default repository settings to apply.
- `repos.<name>`: optional per-repository overrides merged on top of
  `defaults`.

## Supported settings

- `allow_squash_merge`
- `allow_merge_commit`
- `allow_rebase_merge`

All supported settings must be boolean values.
