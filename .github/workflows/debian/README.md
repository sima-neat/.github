# Vulcan Debian publication workflow

`vulcan-publish-debian.yml` is the caller-facing workflow for Debian packages.
The package producer builds and tests one `.deb`, uploads it as a GitHub Actions
artifact, then calls this workflow. This workflow validates the artifact and
submits immutable bytes plus provenance. It does not sign or edit the APT tree.

The Vulcan intake/state-machine handoff described below is **not yet provisioned**
by Vulcan #218. Until it is deployed, the shared workflow is a contract and
cannot publish packages. Do not configure a source repository with the existing
Vulcan channel publisher role: that role has signing and repository-write access.

## Caller contract

Pin the reusable workflow to a reviewed commit SHA. The local `$/debian-publish`
composite action resolves from the same commit as the called workflow, including
when the caller pins the workflow. The caller must grant `actions: read` and
`id-token: write`; it must not grant `contents: write` or pass secrets. Upload
exactly one `.deb` into the named artifact. Supply its SHA256 from the build job,
not a value computed from the downloaded artifact in the publish job.

The shared workflow accepts `channel`, `suite`, `architecture`, `artifact_name`,
`expected_sha256`, `package_name`, `package_version`, `source_repository`,
`source_ref`, `source_commit`, `build_sequence`, `build_provenance`,
`lifecycle_class`, `submission_role_arn`, `intake_bucket`,
`publisher_state_machine_arn`, `channel_url`, `archive_key_fingerprint`, and
`archive_key_url`. `aws_region` defaults to `us-west-2`. Supported suites are
`bookworm` and `agate`; architectures are `amd64`, `arm64`, and `all`.

`source_repository`, `source_ref`, and `source_commit` must equal the GitHub
caller context. `build_sequence` must be `<github.run_id>.<github.run_attempt>`.
`build_provenance` is a nonempty JSON object. `develop` requires a branch ref
and `branch` or `release-candidate` lifecycle. Its package version must contain
the branch token, build sequence, and first 12 commit hex digits. The token is
a lowercase, punctuation-normalized branch slug plus 12 hex digits of SHA256
of the *original* branch name; two branches that normalize to the same slug
therefore remain distinct. `official` requires `refs/tags/v<package_version>`
and `official` lifecycle. Caller-side checks improve feedback; the central
publisher must repeat all security-relevant validation.

The workflow returns `published_version`, `package_url`, `package_sha256`,
`channel_url`, and `branch_token`. The immutable manifest also records the
original ref, commit, build sequence, provenance, and lifecycle class for
branch discovery and retention. Package publication only succeeds after the
central execution succeeds and the public endpoint serves a correctly signed,
unexpired `InRelease`, a matching `Packages` index, and the exact package bytes.

## Staging caller sketch

```yaml
permissions:
  actions: read
  id-token: write

jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      sha256: ${{ steps.digest.outputs.sha256 }}
      version: ${{ steps.version.outputs.version }}
    steps:
      # Build and test ./dist/sima-cli.deb, and set version output from dpkg-deb --field.
      - id: digest
        run: echo "sha256=$(sha256sum ./dist/sima-cli.deb | cut -d ' ' -f1)" >> "$GITHUB_OUTPUT"
      - uses: actions/upload-artifact@v4
        with:
          name: sima-cli-deb
          path: ./dist/sima-cli.deb
          if-no-files-found: error

  publish-develop:
    if: github.event_name == 'push' && startsWith(github.ref, 'refs/heads/')
    needs: build
    uses: sima-neat/.github/.github/workflows/vulcan-publish-debian.yml@<reviewed-commit-sha>
    with:
      channel: develop
      suite: bookworm
      architecture: amd64
      artifact_name: sima-cli-deb
      expected_sha256: ${{ needs.build.outputs.sha256 }}
      package_name: sima-cli
      package_version: ${{ needs.build.outputs.version }}
      source_repository: ${{ github.repository }}
      source_ref: ${{ github.ref }}
      source_commit: ${{ github.sha }}
      build_sequence: ${{ github.run_id }}.${{ github.run_attempt }}
      build_provenance: '{"build_system":"sima-cli-ci"}'
      lifecycle_class: branch
      submission_role_arn: ${{ vars.VULCAN_DEBIAN_STAGING_DEVELOP_SUBMISSION_ROLE_ARN }}
      intake_bucket: ${{ vars.VULCAN_DEBIAN_STAGING_INTAKE_BUCKET }}
      publisher_state_machine_arn: ${{ vars.VULCAN_DEBIAN_STAGING_DEVELOP_STATE_MACHINE_ARN }}
      channel_url: https://debian.stg.neat.sima.ai/develop
      archive_key_fingerprint: ${{ vars.VULCAN_DEBIAN_STAGING_DEVELOP_KEY_FINGERPRINT }}
      archive_key_url: https://debian.stg.neat.sima.ai/keys/develop-2026.asc
```

For an official release, call the same workflow from a tested `v2.1.18` tag,
set `channel: official`, `lifecycle_class: official`, and the official role,
state machine, URL, key fingerprint, and key URL. The built control version must
be `2.1.18`. Branch builds should use a Debian version such as
`2.1.18~dev.feature-x-<branch-hash>.<run-id>.<attempt>.<commit12>`; compute the
branch token with the algorithm in `debian-publish/publish.py` before building.
The sima-cli producer wiring and exact version generator belong to sima-cli #246.

## Security and Vulcan handoff

For each producer and channel, Vulcan needs a distinct OIDC role trusted only
for the producer repository and its protected `debian-apt-develop` or
`debian-apt-official` GitHub environment. Restrict that environment to approved
refs; official should require release approval. Fork and PR events cannot reach
this workflow's AWS step. The role may `PutObject` with `If-None-Match: *` only
under `submissions/<owner>/<repo>/<channel>/`, and may start/read only the
channel's intake state machine. It must have **no** served-bucket write,
`dists/` write, signing-secret read, or arbitrary CodeBuild/Step Functions
execution override. IAM must also restrict package names for each producer at
the trusted publisher, because S3 prefixes and signing keys cannot enforce that.

The workflow writes `package.deb` and `manifest.json` to a unique immutable
`submissions/<owner>/<repo>/<channel>/<submission-id>/` prefix, then starts a
Standard Step Functions execution named by that ID. Input contains only
`schema_version`, `manifest_bucket`, `manifest_key`, `package_key`, and
`submission_id`. Vulcan must validate input keys against the authenticated
producer's namespace, rehash and parse the `.deb`, enforce package ownership,
ref/channel policy and duplicate-version rules, and run one authoritative
publisher per channel under the existing DynamoDB channel lock. A successful
execution returns JSON with `channel_url`, `package_url`, `sha256`,
`suite`, `architecture`, `package_name`, `package_version`, `source_commit`,
and `branch_token`. The
state machine must not return success until indexing/signing is complete and
public verification succeeds. An execution failure or 30-minute timeout is a
caller failure; retain immutable intake and execution logs for retry/audit.
Retry a failed producer run with a new run attempt. Vulcan should reconcile a
failed execution before retrying publication, since a failure can occur after
`InRelease` is committed. A different digest for the same package/version/arch
must remain a hard collision error; an identical replay may be idempotent.

GitHub's per-repository concurrency does not serialize different producer
repositories. The trusted Vulcan publisher must hold the channel lock through
index creation, signing, and publication. The same lock must cover retirement
and garbage collection. Intake objects should have an explicit lifecycle policy
independent of the served pool; the signed inventory must preserve original
branch/ref/commit/build sequence/lifecycle for discovery and retention.

To onboard a second producer, register its package-name ownership, OIDC role,
GitHub environments/ref rules, and intake namespace in Vulcan; then use this
same pinned workflow. Never share a submission role between unrelated source
repositories. Run focused allow/deny contract tests here and concurrency,
replay, collision, and fail-after-commit tests in Vulcan before enabling either
channel for a producer.
