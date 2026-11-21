# Select a release SDK or resolve a development sysroot

## Daily and pre-release builds

Call `.github/workflows/resolve-sysroot.yml` before the build job. The workflow
checks out the caller at the tested commit and reads `deps/manifest.json`:

```json
{
  "platform-version": "3.0.0",
  "elxr_version": "3.0.0_master_B2263"
}
```

Use `needs.resolve-sysroot.outputs.sysroot_version` as the exact version passed to
`sysroot update` for development provisioning. The workflow also returns
`elxr_version` for consumers that need the requested SDK firmware identity. It
does not select runners or update devices.

Pin the workflow reference and `shared_workflow_ref` input to the same reviewed
`.github` commit. The latter selects the resolver script checkout. The default
script reference is `main`; callers testing an unmerged workflow must explicitly
select its script revision too.

The resolver supports Modalix eLxr 3.0 master and develop builds. For a
`3.0.<patch>` firmware base, master uses the `pre-release` Agate repository and
matches `3.0.<patch>~preBUILD`. Develop uses `daily` and matches
`3.0.<patch>~gitTIMESTAMP.HASH-BUILD`. It selects only the
`simaai-palette-modalix` platform package. The SDK channel is not a firmware branch
selector.

To avoid stale flat `Packages.gz` caches, resolution follows the SHA-256 reference
in `Release` to the immutable by-hash index and verifies both size and digest.
Requests use HTTPS. This checksum verifies consistency with `Release`; it is not
an independent signature verification. Missing builds, multiple distinct matching
versions, invalid input, and checksum failures stop resolution without choosing a
newer or fallback build. A `sysroot-resolution` artifact records the input, output,
repository channel, and index digest for reproducibility. Builds require continued
retention of the matching packages in the mirror.

The deprecated exact `sysroot-version` manifest field remains supported with a
warning for older callers; it is returned as an explicit pin without a mirror
lookup. New callers must use `elxr_version`; specifying both fields is an error.

For a local lookup:

```sh
python3 scripts/resolve_sysroot.py --manifest path/to/deps/manifest.json
```

## Official release SDKs

Use an SDK image built and published for the official Platform release. Release
SDK builds obtain platform packages from `https://repo.sima.ai/elxr/deb/release`
(the `agate` suite for Platform 3). Pin the matching published SDK artifact in
the caller's dependency manifest or SDK cache configuration. Package publication
alone does not establish that a matching SDK image has been published.

When moving from a development sysroot to an official release, update the SDK
pin and platform version together. Replace development sysroot provisioning
with validation of the published SDK. Use `sysroot update` only for daily and
pre-release provisioning. Select firmware from the release's published artifacts
and retain the normal product build and DevKit qualification checks.

Consumers using a published release SDK declare the exact package version under
`sdk.sysroot-version`, alongside the firmware identity:

```json
{
  "platform-version": "3.0.0",
  "elxr_version": "3.0.0_master_B2499",
  "sdk": {"channel": "develop", "sysroot-version": "3.0.0"}
}
```

The same shared workflow returns `sysroot_version=3.0.0`, preserves the B2499
`elxr_version`, and records `source=release-sdk-manifest` and `channel=release`.
Use `sysroot_version` to validate the running release SDK.
It does not search the pre-release/daily index or silently fall back if the pin
is malformed. The pin must match the master firmware base; develop firmware,
conflicting top-level legacy pins, nulls and development package versions are
rejected. Without a nested release pin, existing verified development resolution
and legacy behavior remain unchanged.

Before building, verify the running SDK's `/etc/sdk-release` platform version,
installed platform package inventory, payload ownership, and firmware identity.
Use the separate shared Modalix runner resolver's `devkit_firmware` output for
the DevKit target and keep `elxr_version` as the requested SDK firmware identity.
