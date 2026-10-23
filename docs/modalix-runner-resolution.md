# Modalix runner selection

`vulcan-resolve-modalix-runner.yml` selects the runner group from
`platform-version` and reads optional `elxr_version` from the caller's dependency
manifest. For example, `3.0.0_master_B2263` requires that exact GitHub runner
label. Master and develop identities are distinct.

Pass every required hardware and environment label in the `runner-labels` JSON
array. Use `modalix_runner_group` and `fromJSON(...runner_labels)` together in
the consuming job's `runs-on`. Looking up a runner is not a reservation; GitHub
queues the job against those labels. Busy online runners remain eligible.

`allow-version-fallback` defaults to `false`. If no online runner in the selected
group has all labels including the requested firmware, resolution fails. When
explicitly enabled, fallback removes only the firmware requirement and preserves
all hardware and environment constraints. The resolver emits a warning and
`version_fallback: true`. Offline runners are not eligible. Missing credentials,
API errors, invalid manifests, and inaccessible groups always fail; they never
trigger fallback. Manifests without `elxr_version` retain platform-group behavior.

Pass the existing GitHub App credentials as `app_id` and `app_private_key`.
Internals and LLiMa use the Release Engineering App secrets `NEAT_RELEASES_APP_ID`
and `NEAT_RELEASES_APP_PRIVATE_KEY`. The App must have organization
self-hosted-runners read permission. Caller repository visibility comes from
GitHub Actions event metadata; the App needs no private repository content access.
The workflow mints a short-lived token limited to runner read permission and
revokes it at job completion. `RUNNER_DISCOVERY_TOKEN` is an alternative for
callers that already supply an appropriate token.
The ordinary workflow `GITHUB_TOKEN` does not grant organization runner discovery.
The resolver paginates the selected group's inventory and checks its repository
access. Its `runner-resolution` artifact records requested firmware, selected
labels, fallback status, and eligible runner names. No credentials are recorded.

The resolver is bundled directly in the reusable workflow, so pinning the workflow
to a tag or commit also pins the executed resolver code. No separate shared
repository checkout, new caller input, or additional permission is needed.
`shared_workflow_ref` remains an optional, ignored compatibility input for callers
that already supply it. Existing callers may continue omitting it.

Maintainers edit `scripts/resolve_modalix_runner.py`, then run
`python3 scripts/bundle_modalix_resolver.py` to refresh the bundled copy. CI checks
that the copy matches the source and runs the resolver tests against both copies.

On the selected DevKit, verify `/etc/build` or `/etc/buildinfo` before installing
test packages, so stale runner labels fail visibly. Selection and this verification
do not update firmware. Fallback cannot guarantee ABI compatibility and should be
used only when the caller's tests support mixed firmware.

Consumers that derive firmware from a built dependency can pass
`manifest_artifact` and `manifest_path`. The resolver downloads that artifact
from the current workflow run instead of reading the repository manifest. The
caller must depend on the producing build job. Core uses `neat-core-dist` and
`internals-manifest.json` so runner selection follows the exact Internals package
consumed by its build, including any dependency fallback.
