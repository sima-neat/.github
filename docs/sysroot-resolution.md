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

The resolver supports Platform 3 master and develop firmware identities. It
reads the requested identity from the caller's manifest. Develop builds match
`BASE~gitTIMESTAMP.HASH-BUILD` in `daily`. For master builds it searches the exact
`BASE~preBUILD` in `pre-release` and release versions matching the manifest's platform base (including Debian
revision/local suffixes).
A release candidate is accepted only after downloading its matching indexed
base-files DEB, verifying its size/SHA-256, and reading the exact requested
`SIMA_PACKAGE_VERSION` from `/etc/packageinfo`. Missing or ambiguous matches,
including a match in both master channels, fail rather than choosing latest.
The SDK channel is not a firmware branch selector.

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

## Discover and provision promoted releases

The caller manifest is the source of the firmware pin. There is no shared
build-number mapping, package-version constant or package-hash allowlist.
For example, a manifest requesting `3.0.0_master_B2479` currently discovers
Palette `3.0.0` in the release mirror because the matching base-files payload's
`/etc/packageinfo` identifies exactly that firmware. A newer or different build
is not an acceptable substitute. Other platform bases/build numbers follow the
same discovery code.

The resolution receipt records the selected firmware, channel, exact APT version,
immutable index digest/URL, and the Palette/base-files version, architecture,
filename, size and SHA-256 from that index. The provisioner consumes this receipt
and checks it against the manifest-derived firmware/version supplied by the
caller. Consumers do not maintain another firmware-to-APT mapping.

Cached SDKs can resolve stable Agate packages, but their `sysroot update` entry
point accepts only `~pre`/`~git` versions. `prepare_release_sysroot.py` adapts
temporary copies of those installed entry points for the receipt's exact version,
uses the release mirror in an isolated APT cache, and preserves the installed
directory transaction, dependency/archive validation, inventory generation,
finalization and compiler/link probes. Release components such as TVM retain
upstream `~git` versions; their membership in the isolated release repository
establishes eligibility. Installed tools and `/etc/sdk-release` stay unchanged.
Unsupported SDK interfaces fail; no SDK source download or alternate channel is used.

The shared `setup-sdk-package-helper` action and `vulcan-build.yml` export
`SYSROOT_RELEASE_HELPER`. Copy it into the disposable SDK and retain the resolver's
`sysroot-resolution.json` artifact under `/workspace/dist/ci`. Kernels opts into
the shared build workflow's `sysroot_resolution_artifact` input to download this
same-run artifact before provisioning. Run the helper as root, passing values
read from the manifest/resolution outputs, for example:

```sh
SIMA_DISPOSABLE_SDK=1 SYSROOT_UPDATE_DOWNLOAD_DIR=/tmp/provider-debs \
  python3 /tmp/prepare-release-sysroot.py \
    --elxr-version "$REQUESTED_FIRMWARE" --sysroot-version "$RESOLVED_SYSROOT" \
    --receipt /workspace/dist/ci/sysroot-resolution.json
```

After provisioning, the helper checks the actual Palette inventory and requested
firmware identity, and verifies both downloaded Palette/base-files archives
against the receipt's sizes/hashes. Keep the downloaded provider DEBs for the
consumer's ownership checks. This establishes provisioning and a compiler/link
smoke probe, not product or physical hardware qualification.

### Verified provisioning example

On 2026-10-28 (America/Los_Angeles), the helper above completed against disposable
ARM64 image `ghcr.io/sima-neat/sdk-develop:latest`, image ID
`sha256:66c8623e7faa62850b8078e498417029d64244f09bfba9cfe3214de1e86d5478`.
The image's initial platform was `3.0.0~pre2372`. It resolved, downloaded,
extracted and validated 934 packages, passed the installed SDK's compiler/link
probe, and activated release sysroot `3.0.0`. The final inventory and
`/etc/packageinfo` matched B2479; the downloaded base-files hash matched the
verified resolution receipt. The installed updater, setup helper, downloader and
`/etc/sdk-release` hashes matched a fresh container from the same image.

The manifest-driven live resolver used release index SHA-256
`cdb96bf2587e9e35a6644d0f6f24ae2ff00b0fe0e48cad0d6c5066822004adbc`.
This is provisioning evidence, not a product build, DevKit run, or performance
qualification. Re-run the commands for a new image or changed package index.
