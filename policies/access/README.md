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
| `admin` | `security_manager` |
| `dev` | `all_repository_read` |
| `devops` | `ci_cd_admin`, `apps_manager` |
| `cloud` | none |
| `platform` | none |
| `ae` | none |
| `qa` | `all_repository_read` |
| `model-developers` | none |
| `model-tools-dev` | none |
| `owners-model-tools` | none |
| `owners-code` | none |

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

Prefer `maintain` only for repo stewardship and release/operations ownership.
Use `push` or `write` for code contribution without repo settings control.

The current model keeps day-to-day contributor teams on `write` unless they have
an explicit stewardship responsibility. This includes:

- `dev`
- `platform`
- `ae`
- `qa`

Release and operations stewardship is represented separately through `devops`
and `admin` repo grants.

The `model-developers` team has write access to `models` for model development.

The `model-tools-dev` team has write access to `model-tools`. The
`owners-model-tools` team has maintain access for use as
`@sima-neat/owners-model-tools` in that repository's CODEOWNERS file.

The `dev` and `owners-code` teams have maintain access to `code`.
The `owners-code` team owns the repository's files in CODEOWNERS.

The `cloud` team has maintain access to `vulcan`, `arthas`, and `kerrigan`.

The private `blogs` repository uses the following team grants:

| Team | Repository permission |
|---|---|
| `dev` | write |
| `admin` | maintain |
| `devops` | maintain |
| `owners-blogs` | maintain |

The `owners-blogs` team owns all repository files through
`@sima-neat/owners-blogs` in CODEOWNERS. Its initial maintainer is
`shanguangzai`; team membership is managed in GitHub rather than by this policy.
Blog reader access will be managed separately through the site's SSO and does
not require GitHub repository access.

The `linux` repository holds the SiMa.ai Linux kernel. It stays private
until it is published, and uses the following team grants:

| Team | Repository permission |
|---|---|
| `owners-linux` | maintain |
| `reviewers-linux` | write |

The `owners-linux` team owns all repository files through
`@sima-neat/owners-linux` in CODEOWNERS. Team membership is managed in GitHub
rather than by this policy. Pull requests into its protected branches need two
total approvals, including a required code-owner approval
(`policies/org-rulesets/require-two-approvals.json`). These can come from owners
and contributors with write access; there is no separate per-team quota. Ensure
there are enough eligible reviewers other than the author.

`reviewers-linux` can contribute code as well as submit qualifying reviews;
GitHub write is not a review-only permission. Membership is managed in GitHub.
The independent per-repository update restriction in
`policies/org-rulesets/owner-only-merge/` limits merges into Linux main to its
owner team (plus the existing organization-admin bypass). Owner-team bypass is
pull-request-only and applies only to that update rule, not review requirements.
Complete the scratch acceptance in the ruleset README before rollout.

The `qa` team has organization-wide read access and write access for repos
where QA needs to contribute tests, issues, and validation work:

- `apps`
- `insight`
- `sima-cli`
- `playbooks`

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

## GitHub App authentication

App: [`palette-org-admin`](https://github.com/organizations/sima-neat/settings/apps/palette-org-admin)
(App ID `5895412`), installed on `sima-neat` as installation `185181039`.

The apply job uses a dedicated organization-owned GitHub App installation token.
It does not fall back to `ORG_ADMIN_TOKEN` or `GH_TOKEN` secrets. Report mode
requires no App credentials. Apply jobs are serialized to avoid overlapping
policy changes.

Use the private App `palette-org-admin`, owned by `sima-neat`, with the
following permissions:

| Scope | Permission | Level | Used for |
|---|---|---|---|
| Organization | Members | Read and write | Create/update teams and assign organization roles |
| Organization | Administration | Read and write | Enforce organization rulesets through the separate ruleset workflow |
| Organization | Custom organization roles | Read-only | Look up organization role IDs |
| Repository | Administration | Read and write | Grant/remove team repository access |
| Repository | Metadata | Read-only (automatic) | Repository metadata and team visibility |

The ruleset workflow reuses this App configuration. Its token requests only
organization Administration write and Members read, while the Access Policy
workflow requests its existing repository Administration, Members and custom-org-role
permissions. Approve the new organization Administration permission on the
installation before verifying ruleset API access. That permission is distinct
from repository Administration.

Use this repository's URL as the homepage. Disable webhooks; no webhook events,
callback URL, or user authorization flow is needed. Keep this App separate from
release-engineering and NeatOps Apps because access-policy administration
requires different permissions.

GitHub documents installation-token support for both
[organization roles](https://docs.github.com/en/rest/orgs/organization-roles)
and [teams and repository grants](https://docs.github.com/en/rest/teams/teams).

Before merging the authentication switch:

1. Register the App under the
   [organization's GitHub App settings](https://github.com/organizations/sima-neat/settings/apps).
2. Install it on `sima-neat` with access to **All repositories**, so existing
   policy entries and future managed repositories are covered. This grants
   repository Administration write across the installation, so use a dedicated
   App and protect its key accordingly.
3. In the `.github` repository's `org-policy` environment, set the variable
   `ACCESS_POLICY_APP_CLIENT_ID` to the App's Client ID.
4. Generate a private key in the App settings and save it directly as the
   environment secret `ACCESS_POLICY_APP_PRIVATE_KEY`. Do not put the key in
   source control or logs.
5. Merge the workflow change, then verify the apply run logs the expected App
   slug and installation ID and completes successfully. The push to `main`
   triggers an apply automatically; a manual apply can be used to recheck.
6. Remove the old personal-token secret from this workflow's configuration
   only after checking for other consumers. Revoke the personal token only
   after migrating any other automation that uses it.

The token is generated by
[`actions/create-github-app-token`](https://github.com/actions/create-github-app-token),
expires after one hour, and is revoked at job completion. The privileged token
is supplied only to the apply step; checkout uses the normal read-only workflow
token.

Switching identities does not eliminate GitHub secondary rate limits. The
sync script spaces writes by at least one second and retries explicit HTTP
429 or rate-limit HTTP 403 responses up to three times. It honors `Retry-After`
and exhausted primary-quota reset headers, otherwise waiting at least one
minute with exponential backoff. Retry waits have a total budget of ten minutes;
a longer server-requested wait fails the run instead of retrying early.
Permission failures and ambiguous network failures are not retried. Each apply
still writes every configured grant; skipping unchanged grants is a future
optimization. See
[GitHub API best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).

## Validation

Run these checks before opening a PR:

```bash
jq empty policies/access/config.json
python3 scripts/sync-access-policy.py --mode report
python3 -m py_compile scripts/sync-access-policy.py
python3 -m unittest discover -s tests -p "test_access_policy*.py"
actionlint .github/workflows/apply-access-policy.yml
git diff --check
```

The `Apply Access Policy` workflow validates and summarizes this policy on PRs.
It applies the policy on pushes to `main`, scheduled runs, and manual apply
runs.

## Migration Plan

1. Move users into `admin`, `dev`, `devops`, `platform`, `ae`, and `qa`.
2. Remove direct assignments in GitHub after equivalent team membership is in
   place.
3. Remove `argo` and `alpha` grants after migration is complete.
4. Delete `argo` and `alpha` from `legacy_teams` once they no longer need
   protection from pruning.
5. Enable `prune_unmanaged.teams` only after the policy fully represents
   intended team access.
