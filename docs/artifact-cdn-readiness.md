# Artifact CDN readiness

The shared `vulcan-publish-artifacts.yml` publisher gates dependent test jobs on
CDN verification for SiMa-owned publications with a configured CloudFront
distribution. Before uploading, it checks that resources in every `metadata.json`
and `metadata-*.json` file, including selectable resources, are included in the
publication and match their declared checksums when present.

After uploading, the publisher checks every published file through the configured
artifact base URL. Small package metadata and manifest files receive full GETs
with SHA-256 verification. Other files receive `Range: bytes=0-0` GET requests;
verification requires HTTP 206, one readable byte, and a valid Content-Range
header. The probe reads at most one byte and fails if the server ignores Range.
This exercises the download path without downloading entire packages. It checks
availability, not package integrity; clients retain responsibility for verifying
package checksums. CloudFront may fetch a larger range from the origin than the
viewer requests.

Each file has four attempts with 5, 10, and 15 second retry delays. An existing
commit prefix is invalidated before verification. For a new prefix, failed files
are retried after one prefix-scoped invalidation. Persistent failures fail the
publish job, blocking dependent tests.

A successful metadata request alone cannot release the test fleet. Verification
covers the publisher's CDN route at publication time, so clients still need to
handle later transient errors or failures at other CDN edges.

Cross-owner publications retain their existing behavior and do not receive
CloudFront invalidation permissions.
