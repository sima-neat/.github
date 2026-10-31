# CI failure analysis on the bridge

Vulcan failure notifications post their parent Slack message first. The shared
`reusable-failure-analysis.yml` then collects evidence for the exact run attempt,
uses Codex CLI on the self-hosted `bridge` runner to produce a JSON diagnosis,
and replies in the original Slack thread. If diagnosis fails, the reply states
that analysis is unavailable; the original notification is still delivered.

## Runner setup

Install Codex CLI for the account running the GitHub Actions runner. Version
`0.160.0` is validated for this workflow. Ensure `codex` is on the runner service's
PATH, then authorize that same account on the bridge:

```sh
codex login
codex login status
```

The status must report `Logged in using ChatGPT`. A login under a different user
or a different `CODEX_HOME` does not authorize the runner. The analyzer uses the
native login directly; it does not read, copy or export OAuth tokens. It does not
use `OPENAI_API_KEY`. The optional `openai_api_key` workflow secret is retained as
an ignored compatibility input so existing callers keep working.

## Diagnosis execution

The analyzer runs `codex exec` with model `gpt-6.1-sol`, a read-only sandbox,
noninteractive approvals, an ephemeral session and the checked-in JSON output
schema. It starts in a temporary directory outside the checkout and ignores
personal configuration. Shell execution, Code Mode execution, connected apps,
plugins, hooks, browser/computer tools, web search and subagents are disabled.
Only a small environment allowlist reaches the process; workflow GitHub, Slack
and API-key secrets are omitted. Evidence is supplied through stdin as untrusted
data. The script validates the final report and rejects tool events or incomplete
sessions. The expected Codex 0.160 notice about the disabled Code Mode executor
is accepted; other error items fail analysis.

The CLI call has a ten-minute timeout. Failures produce a bounded diagnostic
without printing raw CLI output or evidence into workflow logs. Temporary files
are removed on exit; `--ephemeral` avoids retaining session rollout files.

## Validation and troubleshooting

PR tests run mocked CLI cases on GitHub-hosted runners. Live synthetic smoke runs
only after changes reach protected `main`, on the same `bridge` runner label as
production, without sending a Slack message. It requires an authorized Codex login
and fails visibly if that login is missing.

For login failures, check `codex login status` as the runner service account with
its actual HOME and CODEX_HOME. For CLI failures, verify the installed version and
run the synthetic smoke. ChatGPT usage limits still apply to the authorized
session; switching authentication does not guarantee unlimited capacity.
