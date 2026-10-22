# Org Rulesets Policy

Use this directory to define organization-wide GitHub rulesets. Each `*.json`
file is applied independently by ruleset `name`, so branch-specific behavior
should be split into separate files when the rules differ.

The sync script creates or updates rulesets by `name`. Tokens must have
organization administration write permission. Use a dedicated `ORG_ADMIN_TOKEN`
stored in the `org-policy` environment; the workflow intentionally does not
fall back to a generic GitHub token.

Branch protection rulesets should start in `evaluate` mode for initial rollout.
After rule insights show the expected impact, change `enforcement` to `active`.

The current merge method policy is:

- `develop`: squash merge only
- `main`: merge commit only
- `release-*` and `v[0-9]*.[0-9]*.[0-9]*`: protected by the shared review/deletion/non-fast-forward ruleset

All three policies target `~ALL` repositories, including newly created repositories.
Keep `repository_name.protected` set to `false`: GitHub rejects `~ALL` when
repository-name protection is enabled. This flag protects repository names;
it does not disable the branch rules. Do not replace `~ALL` with a snapshot of
repository IDs, which would leave future repositories unprotected.

The policies preserve the existing organization-admin bypass and require an
extra approval for unattributed changes. Repository-specific rules remain
additive to this shared baseline.

After changing a policy, verify the Enforce Org Rulesets workflow succeeds and
check inherited rules using `gh api repos/sima-neat/<repository>/rulesets`.
