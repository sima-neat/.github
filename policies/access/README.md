# Access Policy

This directory is the source of truth for `sima-neat` organization access.

The policy is intentionally team-centric. Repository access should be granted to
teams, not directly to individual users.

## Status

Current mode: enforced through the `Apply Access Policy` workflow.

The old repo-centric `repo-permissions` policy has been retired. Add team,
organization-role, and repository access here.

## Policy Model

`config.json` has three main sections:

- `teams`: managed team definitions, organization roles, and repository grants.
- `legacy_teams`: existing teams preserved during migration.
- `prune_unmanaged`: future enforcement behavior for access that is present in
  GitHub but absent from policy.

## Teams

Each team entry is keyed by its GitHub team slug. The `name` field must match
that slug.

```json
"dev": {
  "name": "dev",
  "description": "Development",
  "privacy": "closed",
  "org_roles": ["all_repository_read"],
  "repos": {
    "core": "maintain"
  }
}
```

Use `description` for the human-readable team name.

GitHub generates slugs from team names, so the team name is intentionally short
and stable. For example, the `platform` team has description `Platform
Development`.

## Organization Roles

`org_roles` defines organization-level roles to assign to managed teams.

Supported role identifiers:

- `all_repository_read`
- `all_repository_write`
- `all_repository_triage`
- `all_repository_maintain`
- `all_repository_admin`
- `apps_manager`
- `ci_cd_admin`
- `security_manager`

Current intended assignments:

| Team | Organization Roles |
|---|---|
| `admin` | `all_repository_admin`, `security_manager` |
| `dev` | `all_repository_read` |
| `devops` | `ci_cd_admin`, `apps_manager` |
| `platform` | none |
| `release` | none |
| `ae` | none |

Avoid all-repository write, maintain, or admin unless there is a specific
reviewed need. Prefer repo-specific grants.

The `admin` team is the explicit exception: it has `all_repository_admin` so
administrators can manage every current and future repository without keeping a
manual repo list in this policy. It also has `security_manager` for
organization-level security policy, alert, and configuration administration.

## Repository Permissions

Repository grants live under each team in `teams.<slug>.repos`.

Supported permissions:

- `pull` or `read`
- `triage`
- `push` or `write`
- `maintain`
- `admin`

Prefer `maintain` for repo stewardship and `push` for code contribution without
repo settings control.

Use `repos` for repo-specific access. Use `org_roles` for access that is meant
to apply across the organization.

The current model intentionally limits `ae` maintain access to:

- `sima-cli`
- `apps`
- `docs`

## Legacy Teams

`argo` and `alpha` are preserved during migration to avoid disrupting current
work.

Do not add new long-term access through legacy teams. New access should be added
to one of the managed teams in `teams`.

Legacy teams can be removed after:

1. Members are moved to the new teams.
2. Repo grants are represented in `teams`.
3. The access report shows no unmanaged access that should be preserved.

## Pruning

`prune_unmanaged` controls strict enforcement beyond the explicitly managed
grants.

Keep this value `false` during migration:

```json
"prune_unmanaged": {
  "teams": false
}
```

Only set this to `true` after every intentional team grant is represented in
policy. Enabling pruning will remove team access from GitHub if it is not listed
in this policy. Preserved legacy teams are exempt from team pruning while they
remain in `legacy_teams` with `preserve: true`.

## Workflow

1. Add or update team and repository grants in `config.json`.
2. Run `python3 scripts/sync-access-policy.py --mode report`.
3. Review the generated team and repository grant summary.
4. Open a PR. The workflow runs report mode on PRs.
5. Merge to `main`. The workflow applies the policy on push and on the hourly
   schedule.
6. Use manual `workflow_dispatch` with `mode=apply` to repair drift
   immediately.

## Validation

Run these checks before opening a PR:

```bash
jq empty policies/access/config.json
python3 scripts/sync-access-policy.py --mode report
python3 -m py_compile scripts/sync-access-policy.py
actionlint .github/workflows/apply-access-policy.yml
git diff --check
```

The `Apply Access Policy` workflow validates and summarizes this policy on PRs.
It applies the policy on pushes to `main`, scheduled runs, and manual apply
runs.

## Migration Plan

1. Move users into `admin`, `dev`, `devops`, `platform`, `release`, and `ae`.
2. Remove direct assignments in GitHub after equivalent team membership is in
   place.
3. Remove `argo` and `alpha` grants after migration is complete.
4. Delete `argo` and `alpha` from `legacy_teams` once they no longer need
   protection from pruning.
5. Enable `prune_unmanaged.teams` only after the policy fully represents
   intended team access.
