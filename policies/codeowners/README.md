# CODEOWNERS Policy

This directory defines the central ownership mapping for generated repository
CODEOWNERS files.

GitHub reads CODEOWNERS from each target repository and branch. This policy is
the source of truth for generating those per-repo files; it is not itself read
by GitHub.

## Policy Model

`config.json` has six main fields:

- `schema_version`: policy schema version. Current value: `1`.
- `organization`: GitHub organization name.
- `target_repositories`: repositories covered by this policy.
- `default_owner_team`: required owner team for repositories without an
  explicit repository entry.
- `default_watcher_teams`: visibility-only watcher teams for repositories
  without an explicit repository entry.
- `repositories`: repo-specific owner and watcher overrides.

Example:

```json
"apps": {
  "owner_team": "owners-apps",
  "watcher_teams": ["watchers-apps"]
}
```

This means `apps` uses `owners-apps` for required ownership and
`watchers-apps` for visibility-only review requests.

## Owners

Owner teams are required approvers.

Generated CODEOWNERS files should contain owner teams only:

```text
* @sima-neat/owners-apps
```

Owner teams must be defined in `policies/access/config.json` and must have
write-or-higher access to the repository they own. This rollout uses `maintain`
for owner teams.

## Watchers

Watcher teams are visibility-only reviewers.

Watcher teams must not be written into generated CODEOWNERS files. Their
approval must not satisfy the required owner approval gate.

Watcher teams must be defined in `policies/access/config.json` and must have
enough repository access to be requested as reviewers. This rollout uses
`write` for watcher teams.

## Defaults

Repositories listed in `target_repositories` but not in `repositories` use:

- `default_owner_team`
- `default_watcher_teams`

Current repo-specific ownership exists for:

- `.github`
- `apps`
- `core`
- `docs`
- `insight`
- `llima`

All other target repositories use the default owner and watcher teams.

## Validation

Run:

```bash
python3 scripts/validate-codeowners-policy.py
```

The validator checks that referenced teams exist in
`policies/access/config.json`, have compatible repo access, and keep required
owners separate from visibility-only watchers.
