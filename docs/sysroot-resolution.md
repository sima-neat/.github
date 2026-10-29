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

## Platform 3.0 release build B2479

`3.0.0_master_B2479` is a promoted release: its firmware identity remains
`master`, but its Palette package is `3.0.0` in the **release** Agate mirror.
Only this exact firmware identity uses the explicit release mapping. Other
master/develop builds retain their existing channel and version matching rules.
The resolver requires the reviewed Palette and base-files package SHA-256s;
base-files `/etc/packageinfo` reports `SIMA_PACKAGE_VERSION = 3.0.0_master_B2479`.
If these packages are republished with different bytes, resolution fails until
the mapping is reviewed. The resolution artifact includes both package identities.

Cached SDKs can already resolve stable Agate packages, but their `sysroot update`
entry point accepts only `~pre`/`~git` versions. `prepare_release_sysroot.py` adapts
temporary copies of those installed entry points, uses the release mirror in the
SDK's isolated APT cache, and preserves its directory transaction, archive
validation, inventory generation, finalization and compiler/link checks. Release
components such as TVM retain upstream `~git` versions; their membership in the
isolated release repository, rather than a suffix rule, determines eligibility.
The installed tools and `/etc/sdk-release` remain unchanged. An unsupported SDK
interface fails explicitly; no SDK source download or alternate channel is used.

The shared `setup-sdk-package-helper` action and `vulcan-build.yml` export the
revision-pinned helper path as `SYSROOT_RELEASE_HELPER`. Copy it into the disposable
SDK, then run as root with the provider-download directory retained for receipts:

```sh
SIMA_DISPOSABLE_SDK=1 SYSROOT_UPDATE_DOWNLOAD_DIR=/tmp/provider-debs \
  python3 /tmp/prepare-release-sysroot.py \
    --elxr-version 3.0.0_master_B2479 --sysroot-version 3.0.0
```

After provisioning, the helper checks the actual Palette inventory, B2479
`packageinfo` identity and reviewed base-files archive hash. This verifies SDK
provisioning and a compiler/link smoke probe, not product or hardware qualification.

### Verified provisioning example

On 2026-10-28 (America/Los_Angeles), the helper above completed against disposable
ARM64 image `ghcr.io/sima-neat/sdk-develop:latest`, image ID
`sha256:66c8623e7faa62850b8078e498417029d64244f09bfba9cfe3214de1e86d5478`.
The image's initial platform was `3.0.0~pre2372`. It resolved, downloaded,
extracted and validated 934 packages, passed the installed SDK's compiler/link
probe, and activated release sysroot `3.0.0`. The final inventory and
`/etc/packageinfo` matched B2479; the downloaded base-files hash matched the
reviewed mapping. The installed updater, setup helper, downloader and
`/etc/sdk-release` hashes matched a fresh container from the same image.

The live resolver used release index SHA-256
`cdb96bf2587e9e35a6644d0f6f24ae2ff00b0fe0e48cad0d6c5066822004adbc`.
This is provisioning evidence, not a product build, DevKit run, or performance
qualification. Re-run the commands for a new image or changed package index.
