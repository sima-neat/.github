# Org Rulesets Policy

Use this directory to define organization-wide GitHub rulesets.

The sync script creates or updates rulesets by `name`. Tokens must have
organization administration write permission. Use a dedicated `ORG_ADMIN_TOKEN`
stored in the `org-policy` environment; the workflow intentionally does not
fall back to a generic GitHub token.

Branch protection rulesets should start in `evaluate` mode for initial rollout.
After rule insights show the expected impact, change `enforcement` to `active`.
