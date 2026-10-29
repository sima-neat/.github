# Artifact CDN readiness

The shared `vulcan-publish-artifacts.yml` publisher gates dependent test jobs on
CDN verification for SiMa-owned publications with a configured CloudFront
distribution. Before uploading, it checks that resources in every `metadata.json`
and `metadata-*.json` file, including selectable resources, are included in the
publication and match their declared checksums when present.

After uploading, the publisher downloads every published file through the
configured artifact base URL and compares SHA-256 against the local source.
Downloads are streamed in 1 MiB chunks. Each file has four attempts with 5, 10,
and 15 second retry delays. An existing commit prefix is invalidated before
verification. For a new prefix, failed files are retried after one prefix-scoped
invalidation. Persistent failures fail the publish job, blocking dependent tests.

This verifies packages as well as metadata; a successful metadata request alone
cannot release the test fleet. It checks the publisher's CDN route at publication
time, so clients still need to handle later transient errors or failures at other
CDN edges. Verification adds one full CDN download per published file.

Cross-owner publications retain their existing behavior and do not receive
CloudFront invalidation permissions.
