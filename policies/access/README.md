# Access Policy

This directory defines the planned source of truth for `sima-neat`
organization access.

The policy is intentionally team-centric. Repository access should be granted to
teams first, and direct user assignments should be temporary, documented
exceptions.

## Status

The current `repo-permissions` policy remains the enforcing policy while this
policy is reviewed and validated.

Current mode: report-only through the `Apply Access Policy` workflow.

Do not delete the legacy `repo-permissions` policy until the access report
matches the desired GitHub state and enforcement has been migrated.

## Policy Model

`config.json` has four main sections:

- `teams`: managed team definitions, organization roles, and repository grants.
- `legacy_teams`: existing teams preserved during migration.
- `direct_assignments`: direct user grants that are allowed only as documented
  exceptions.
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

`org_roles` documents intended organization-level roles. These roles are not
enforced by the current report-only script yet.

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
| `admin` | `security_manager` |
| `dev` | `all_repository_read` |
| `devops` | `ci_cd_admin`, `apps_manager` |
| `platform` | none |
| `release` | none |
| `ae` | none |

Avoid all-repository write, maintain, or admin unless there is a specific
reviewed need. Prefer repo-specific grants.

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

## Direct Assignments

Direct user assignments are disabled by default:

```json
"direct_assignments": {
  "allowed": false
}
```

Any direct user grant must be listed under `exceptions` with:

- `username`
- `permission`
- `reason`
- `expires`

Exceptions should be short-lived and reviewed before the expiration date.

## Pruning

`prune_unmanaged` controls future strict enforcement.

Keep both values `false` during migration:

```json
"prune_unmanaged": {
  "teams": false,
  "users": false
}
```

Only set these to `true` after every intentional team grant and direct user
exception is represented in policy. Enabling pruning will remove access from
GitHub if it is not listed in this policy.

## Workflow

1. Add or update team and repository grants in `config.json`.
2. Run `python3 scripts/report-access-policy.py`.
3. Review the generated team and repository grant summary.
4. Move memberships and repo grants in GitHub.
5. Keep `argo` and `alpha` in place until the new teams are populated.
6. Switch enforcement from the old repo-centric policy to this policy once the
   report matches the desired access model.

## Validation

Run these checks before opening a PR:

```bash
jq empty policies/access/config.json
python3 scripts/report-access-policy.py
python3 -m py_compile scripts/report-access-policy.py
actionlint .github/workflows/apply-access-policy.yml
git diff --check
```

The `Apply Access Policy` workflow currently runs in report mode. It validates
and summarizes this policy on PRs that change the policy or its validator. A
future enforcing sync script should be wired into the same workflow before the
old `repo-permissions` workflow is retired.

## Migration Plan

1. Keep the current `repo-permissions` policy as the enforcing source.
2. Use this policy to review the desired team model.
3. Move users into `admin`, `dev`, `devops`, `platform`, `release`, and `ae`.
4. Apply repo grants for the new teams.
5. Remove direct assignments or convert them to short-lived exceptions.
6. Remove `argo` and `alpha` grants after migration is complete.
7. Replace the old repo-centric sync workflow with an enforcing version of this
   policy.
