# Select a release SDK or resolve a development sysroot

## Official releases

For an official Platform release such as `3.0.0`, use an SDK image built for
that release through the normal SDK build and publication path. Release SDK
builds obtain the platform packages from
`https://repo.sima.ai/elxr/deb/release` (the `agate` suite for Platform 3).
Pin the matching published SDK artifact in the caller's dependency manifest or
SDK cache configuration and verify its `/etc/sdk-release` platform version.
Package publication alone does not establish that a matching SDK image has
been built and published.

Do not run the firmware-to-development-sysroot resolver or `sysroot update`
to install the plain release version `3.0.0` into an older cached SDK. Do not
rewrite installed SDK updater scripts to admit release versions. The shared
resolver below is for daily/pre-release builds; it does not discover releases
by inspecting firmware identities in release DEBs.

When moving a consumer from a development sysroot to an official release,
update its SDK pin and platform version together, remove the development
sysroot provisioning step for that release configuration, and retain its
normal product build and DevKit qualification checks. Select firmware through
the release's published firmware artifacts; a `master` build number alone is
not a release SDK selection policy.

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
`sysroot update` for daily/pre-release provisioning. `elxr_version` is also
returned for consumers that need the requested firmware identity. This workflow
does not select runners or update devices.

Pin the workflow reference and `shared_workflow_ref` input to the same reviewed
`.github` commit. The latter selects the resolver script checkout. The default
script reference is `main`; callers testing an unmerged workflow must explicitly
select its script revision too.

The resolver supports Modalix eLxr 3.0 master and develop builds. Master uses the
`pre-release` Agate repository and matches `3.0.0~preBUILD`; develop uses `daily`
and matches `3.0.0~gitTIMESTAMP.HASH-BUILD`. It selects only the
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
