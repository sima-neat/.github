# Resolve sysroot from DevKit firmware

Call `.github/workflows/resolve-sysroot.yml` before the build job. The workflow
checks out the caller at the tested commit and reads `deps/manifest.json`:

```json
{
  "platform-version": "3.0.0",
  "elxr_version": "3.0.0_master_B2263"
}
```

Use `needs.resolve-sysroot.outputs.sysroot_version` as the exact version passed to
`sysroot update` or the repository's provisioning helper. `elxr_version` is also
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
