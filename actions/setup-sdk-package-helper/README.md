# SDK package lock coordination

Use one helper for SDK host package configuration, index refresh and installation.
It validates `/etc/sdk-release` in an explicitly selected container and streams the
pinned script into that container as root. It never runs dpkg or APT on the runner
host or changes the target sysroot. Commands and argument boundaries are preserved.

## Direct jobs and setup scripts

```yaml
- uses: sima-neat/.github/actions/setup-sdk-package-helper@<reviewed-commit>
- run: |
    "$SDK_PACKAGE_HELPER" "$sdk_container" configure
    "$SDK_PACKAGE_HELPER" "$sdk_container" update
    "$SDK_PACKAGE_HELPER" "$sdk_container" install -y --no-install-recommends ninja-build
    "$SDK_PACKAGE_HELPER" "$sdk_container" clean
```

The action sets `SDK_PACKAGE_HELPER` for subsequent steps. Invoke it after
staging a workspace; the installed helper lives under `RUNNER_TEMP`, so source
cleanup does not remove it. Local scripts may instead set `SDK_PACKAGE_HELPER`
to an absolute path to `scripts/sdk-package.sh` in a reviewed shared checkout.
Missing helpers fail closed when package setup is needed.

The reusable `vulcan-build.yml` bundles this same script and exports the same
variable before pre-build/build commands, including with `ensure_sdk_container:
false`. Pin the reusable workflow to a reviewed commit. It does not automatically
run package commands or change existing consumers' dependencies.

## Retry contract

`SDK_PACKAGE_LOCK_TIMEOUT` (default 120 seconds, nonnegative integer) bounds the
elapsed lock retry window **per operation**. `SDK_PACKAGE_LOCK_INTERVAL` (default
5 seconds, positive integer) controls polling. Configuration uses direct dpkg;
APT uses native lock timeout zero so its wait cannot multiply the retry budget.
Known lock failures with the expected exit code are retryable. Unrelated or mixed
errors fail immediately, preserving command output and status. Successful earlier
operations are never replayed after a later failure.

The retry window includes command and diagnostic elapsed time. A package command
that acquires a lock is allowed to finish; the helper does not interrupt a live
package transaction. Therefore this is a lock retry deadline, not a hard runtime
limit on package installation. Normal job timeouts still apply.

Diagnostics snapshot the owner PID if present and package-manager processes in
the same container. Processes may exit between the failure and snapshot. The
helper never deletes locks or signals their owners. Package-list cleanup, when
needed, stays a separate consumer command after successful package operations.

## Maintenance and validation

After editing the helper, run:

```sh
python3 scripts/bundle_sdk_package_helper.py
python3 -m unittest discover -s tests -p test_sdk_package.py -v
shellcheck scripts/sdk-package.sh
python3 tests/sdk_package_integration.py --image <local-sdk-image>
```

The bundle is checked in CI. Integration uses its own disposable container and
real POSIX package locks; it does not touch existing containers or host packages.
See [the consumer audit](../../docs/sdk-package-consumer-audit.md) for rollout scope.
