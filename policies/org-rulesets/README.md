# Org Rulesets Policy

Use this directory to define organization-wide GitHub rulesets. Each top-level `*.json` file defines a ruleset. Files under `owner-only-merge/`
are a template and an explicit repository-to-owner-team mapping, not standalone
rulesets. Branch-specific behavior is split when the rules differ.

The sync script creates or updates rulesets by `name`. Tokens must have
organization administration write permission. Use a dedicated `ORG_ADMIN_TOKEN`
stored in the `org-policy` environment; the workflow intentionally does not
fall back to a generic GitHub token.

Branch protection rulesets should start in `evaluate` mode for initial rollout.
After rule insights show the expected impact, change `enforcement` to `active`.

The current merge method policy is:

- `develop`: squash merge only, except in `linux`, which also allows
  merge commits and rebase
- `main`: merge commit only
- `release-*` and `v[0-9]*.[0-9]*.[0-9]*`: protected by the shared review/deletion/non-fast-forward ruleset

The three shared policies target `~ALL` repositories, including newly created repositories.
Keep `repository_name.protected` set to `false`: GitHub rejects `~ALL` when
repository-name protection is enabled. This flag protects repository names;
it does not disable the branch rules. Do not replace `~ALL` with a snapshot of
repository IDs, which would leave future repositories unprotected.

The policies preserve the existing organization-admin bypass and require an
extra approval for unattributed changes. Repository-specific rules remain
additive to this shared baseline.

## Enroll a repository

1. Add its exact repository name to the inclusion list in
   `require-two-approvals.json`. Initially only `linux` is enrolled. Never use
   `~ALL` or wildcards here. The shared baseline still targets `~ALL`.
2. Keep its CODEOWNERS in that repository. The policy requires two **total**
   approvals, including a required code-owner approval, on `main`, `develop`,
   `release-*`, and `v[0-9]*.[0-9]*.[0-9]*` branches. It dismisses stale approvals,
   requires resolved threads and adds an approval for unattributed Copilot
   changes. It does not require one approval per team.
3. Define `owners-<repo>` and, optionally, `reviewers-<repo>` in
   `../access/config.json`. Owners need write or higher; Linux owners retain
   maintain, reviewers receive write. Apply access policy before resolving a
   newly added owner team. Membership remains managed in GitHub.
4. To restrict merging into `main`, add `"<repo>": "owners-<repo>"` to
   `owner-only-merge/repositories.json`. This enrollment is independent of the
   two-approval list. Do not put team IDs in source: the sync resolves exact
   slugs and stops before any ruleset write if a team or its access-policy grant
   is missing. An API permission failure is also a failure, never an empty team.
5. Complete the scratch acceptance below before enabling this restriction for
   the real repository. Review both workflows and inherited rules after rollout.

`required_reviewers` is a GitHub pull-request rule parameter that supports
minimum approvals per team and file patterns. It can be configured in a future
reviewed policy change if team quotas are desired. It does not replace a stored
ruleset policy, and enabling it now would change the two-total-approvals contract.
See [GitHub's rule API](https://docs.github.com/en/rest/orgs/rules).

The opt-in review policy permits all three merge methods at its own layer, as
before. The separate `require-main-merge-commit.json` and
`require-develop-squash-merge.json` policies still determine effective methods.
No merge-method or baseline policy is changed by enrollment.

## Owner-only merge restrictions

The sync renders **one organization ruleset per repository**, named
`Owner-only merges for <repo>`, from `owner-only-merge/template.json`.
Each includes exactly that repository and `refs/heads/main`, with only an
`update` rule (`update_allows_fetch_and_merge: false`). Its bypass list contains
only the existing OrganizationAdmin/always bypass and that repository's owner
team with `pull_request` bypass. Do not consolidate multiple repositories and
teams into one ruleset: bypass would then cross repository boundaries.

The team bypass applies only to this update restriction. It does not bypass the
separate CODEOWNERS, two-approval, stale-review, resolved-thread or merge-method
requirements. Ordinary reviewers cannot update main even after reviews pass.
Owners can merge through a pull request after the other requirements pass;
they cannot use this bypass for direct pushes. Organization administrators
retain their existing always bypass. Users who are also organization admins
cannot be used as negative-test ordinary owners/reviewers.

The owner permission and approval requirements are separate. A reviewer team
with write permission can push contribution branches and submit qualifying
reviews; the name does not create a review-only GitHub permission.

## Rename migration and verification

`migrations/renames.json` maps the generic `Require two approvals` name to the
legacy `Require two approvals for linux`. Sync searches **all pages** for both
names, updates the legacy ruleset using its existing ID, then reads it back.
There is no delete/create gap. On a subsequent run it finds the canonical name.
If both names or duplicate matches exist, sync stops without modifying either
review ruleset. Investigate explicitly: verify the canonical replacement has
all intended protection before an administrator retires a legacy duplicate;
never delete the old protection first. Unrelated rulesets are not pruned.

Verification compares target, enforcement, all managed conditions, the exact
bypass membership/modes and rule types, and every managed parameter in **every**
rule. It works without a pull-request rule, including update-only restrictions.
API-added object fields/defaults are allowed; managed arrays must match exactly
apart from order. Verification failure stops the run. Ruleset sync jobs are
serialized and never cancel an in-progress sync.

`POLICY_FILE` selects one concrete ruleset JSON for scoped repair; it does not
render the owner template. Normal directory sync renders all mapped owner
restrictions. Do not pass the template or mapping as `POLICY_FILE`.

## Scratch acceptance before Linux rollout

Keep the implementation PR in draft until this live acceptance passes. Merging
changes to this directory triggers live organization enforcement. Offline tests
prove rendering and sync behavior, **not** GitHub authorization.

Use two explicitly named private scratch repositories and two distinct owner
teams. Grant each owner team maintain on its own repository and write on the
other. Give an ordinary reviewer write on both; use distinct non-admin user
identities for the author, owner A, owner B and reviewers. Keep CODEOWNERS in
both scratch repositories. Apply the shared review/baseline/merge-method layers
and render each owner restriction using the scratch names, not Linux. Do not
expand the committed production inclusion list to test repositories.

Record API responses, PR URLs, actor/team memberships, rule IDs, source SHA and
before/after effective rules for this matrix, without tokens:

| Test | Expected result |
|---|---|
| Ordinary PR with no approvals or only one approval | Owner merge rejected |
| Two qualifying approvals including CODEOWNERS | Review requirement satisfied |
| Two approvals without the required code-owner approval | Owner merge rejected |
| New reviewable push after approval | Stale approvals dismissed; merge rejected until reapproval |
| Open review thread with otherwise sufficient approvals | Merge rejected until resolved |
| Reviewer tries to merge an otherwise approved PR | Rejected by update restriction |
| Own repository's owner merges the approved PR | Allowed, using PR-only bypass as GitHub requires |
| Owner A attempts approved PR merge in B, and vice versa | Rejected despite cross-repository write access |
| Ordinary owner/reviewer pushes directly to main | Rejected |
| Organization admin explicitly bypasses | Existing bypass works as documented |
| Second sync and inherited rules readback | Same rule IDs/payloads, no duplicate or missing protection |

Also check effective main/develop merge methods against the pre-test baseline.
Live testing requires authorized organization-rule administration and the
separate non-admin actor sessions; one organization-owner session cannot prove
these denials. Clean up only scratch resources created for the test after
recording results.

After scratch acceptance, roll out access first, then rulesets while Linux is
still private. Verify successful Apply Access Policy and Enforce Org Rulesets
runs, the unchanged legacy review rule ID under its new name, Linux CODEOWNERS,
team access, inherited rules and the same owner/reviewer tests on Linux before
publication. Repository visibility and publication remain separate work.

## Validation

```sh
bash -n scripts/sync-org-rulesets.sh
shellcheck scripts/sync-org-rulesets.sh
python3 -m unittest discover -s tests -p test_org_rulesets.py
python3 -m unittest discover -s tests -p 'test_access_policy*.py'
python3 scripts/sync-access-policy.py --mode report
actionlint .github/workflows/enforce-org-rulesets.yml
git diff --check
```

The ruleset workflow runs offline validation for PRs without administration
credentials; only non-PR events can run the privileged sync job.

After changing a policy, verify the Enforce Org Rulesets workflow succeeds and
check inherited rules using `gh api repos/sima-neat/<repository>/rulesets`.
