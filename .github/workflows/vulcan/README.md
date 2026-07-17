# Vulcan Workflows

Use the Vulcan reusable build workflow when a Neat repository needs to run an
SDK-based build on an ephemeral AWS runner.

The workflow lives in this repository at:

```text
sima-neat/.github/.github/workflows/vulcan-build.yml
```

It requests a Vulcan runner, waits for the scaler to launch the matching EC2
Spot instance, checks out your repository, makes sure the SDK container is
running, and executes the build command you provide.

## Minimal Caller

Add a workflow like this to the repository that needs to build on Vulcan:

```yaml
name: build

on:
  workflow_dispatch:

permissions:
  contents: read
  packages: read

jobs:
  build:
    uses: sima-neat/.github/.github/workflows/vulcan-build.yml@main
    with:
      sdk_cache: sdk-latest
      capacity: default
      build_command: ./build.sh --all --clean
```

## Common Caller With Inputs

This form lets you choose the Vulcan environment, SDK cache snapshot, and runner
size at dispatch time:

```yaml
name: build

on:
  workflow_dispatch:
    inputs:
      vulcan_env:
        description: Vulcan environment override. Empty uses vars.VULCAN_ENV, then dev.
        required: false
        default: ""
        type: string
      sdk_cache:
        description: SDK cache label.
        required: true
        default: sdk-latest
        type: string
      capacity:
        description: Vulcan capacity label.
        required: true
        default: default
        type: choice
        options:
          - cheaper
          - cheap
          - default
          - expensive
          - most-expensive

permissions:
  contents: read
  packages: read

jobs:
  build:
    uses: sima-neat/.github/.github/workflows/vulcan-build.yml@main
    with:
      vulcan_env: ${{ inputs.vulcan_env }}
      sdk_cache: ${{ inputs.sdk_cache }}
      capacity: ${{ inputs.capacity }}
      working_directory: .
      build_command: ./build.sh --all --clean
```

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `vulcan_env` | empty | Vulcan environment override. Empty uses the caller repository or organization `VULCAN_ENV` variable, then `dev`. |
| `sdk_cache` | `sdk-latest` | SDK cache snapshot label requested from Vulcan, such as `sdk-latest` or `sdk-2.1`. |
| `capacity` | `default` | Runner size hint. Supported aliases are `cheaper`, `cheap`, `default`, `expensive`, and `most-expensive`. |
| `working_directory` | `.` | Directory in the checked-out repository where commands run. |
| `build_command` | `./build.sh --all --clean` | Main build command. |
| `pre_build_command` | empty | Optional command to run before `build_command`. |
| `post_build_command` | empty | Optional command to run after `build_command`. |
| `ensure_sdk_container` | `true` | Runs `sima-cli sdk setup` before the build. |
| `sdk_setup_args` | `-y -n --no-model-sdk --no-insight` | Arguments passed to `sima-cli sdk setup`. |
| `fetch_depth` | `1` | Checkout fetch depth. Use `0` when versioning depends on tags or full history. |
| `checkout_submodules` | `recursive` | Value passed to `actions/checkout` for submodule fetching. |

## Release Branches And Draft Releases

Use `vulcan-release.yml` for manually managed release lines that branch from
`main` and publish patch tags from the release branch.

The workflow creates or reuses `release-<release_line>`, creates an annotated
`v<tag>` tag at the release branch HEAD, and creates a draft GitHub release.

Example caller:

```yaml
name: release

on:
  workflow_dispatch:
    inputs:
      release_line:
        description: Minor release line, for example 2.1
        required: true
        type: string
      tag:
        description: Patch release version without v prefix, for example 2.1.2
        required: true
        type: string
      tag_exists_behavior:
        description: Use move only for draft release correction.
        required: false
        default: fail
        type: choice
        options:
          - fail
          - move

permissions:
  contents: write

jobs:
  release:
    uses: sima-neat/.github/.github/workflows/vulcan-release.yml@main
    with:
      release_line: ${{ inputs.release_line }}
      tag: ${{ inputs.tag }}
      source_branch: main
      tag_exists_behavior: ${{ inputs.tag_exists_behavior }}
```

Common inputs:

| Input | Default | Description |
| --- | --- | --- |
| `release_line` | required | Minor release line such as `2.1`; the workflow uses `release-2.1`. |
| `tag` | required | Patch release version without `v`, such as `2.1.2`; must match the release line. |
| `source_branch` | `main` | Source branch used only when creating a new release branch. |
| `branch_exists_behavior` | `reuse` | One of `reuse`, `fail`, or `update_from_source`. |
| `tag_exists_behavior` | `fail` | One of `fail` or `move`; moving is refused if the GitHub release is already published. |
| `release_notes_mode` | `generated` | One of `generated`, `empty`, or `from_file`. |
| `release_notes_file` | empty | File path used when `release_notes_mode` is `from_file`. |
| `prerelease` | `false` | Marks the draft release as a prerelease. |
| `latest` | `true` | Controls GitHub's latest-release marker. |
| `dry_run` | `false` | Resolves and prints actions without pushing branches, tags, or releases. |

## Capacity Labels

| Label | EC2 instance type |
| --- | --- |
| `cheaper` | `c7g.xlarge` |
| `cheap` | `c7g.2xlarge` |
| `default` | `c7g.4xlarge` |
| `expensive` | `c7g.8xlarge` |
| `most-expensive` | `c7g.16xlarge` |

Use `default` for normal SDK builds. Use `cheaper` for smoke tests and small
jobs. Use larger labels only for builds that need the cores or memory.

## Environment Selection

Set an organization-level `VULCAN_ENV=dev` variable during rollout. Individual
repositories can override that variable, or a manually dispatched workflow can
pass `vulcan_env`.

The reusable workflow turns the environment into a runner label:

```text
vulcan-dev
vulcan-staging
vulcan-production
```

Each Vulcan environment should map to its own AWS account, scaler Lambda,
secrets, cache snapshots, IAM roles, and concurrency limits.

## What Happens During A Build

1. Your repository calls `sima-neat/.github/.github/workflows/vulcan-build.yml@main`.
2. GitHub queues a job with labels like `vulcan-dev`, `sdk-build`,
   `sdk-latest`, and `default`.
3. The Vulcan scaler sees the queued job and launches a matching EC2 Spot
   runner.
4. The runner mounts the requested SDK cache snapshot and registers with
   GitHub.
5. The reusable workflow checks out your repository and runs your build command.
6. The runner exits after one job, deregisters, terminates the EC2 instance, and
   deletes the attached cache volume.

## AWS Access From The Build

The reusable build workflow does not request GitHub OIDC and does not publish
artifacts by default. Keep AWS writes in the publish workflows below so build
jobs and publish jobs stay separated.

## Publish Artifacts

Use `vulcan-publish-artifacts.yml` after a build job uploads GitHub Actions
artifacts. The workflow downloads those artifacts, assumes the Vulcan artifact
publisher role through GitHub OIDC, uploads files to S3, and refreshes the
repository branch index.

Published files use this S3 layout:

```text
s3://<bucket>/<repo>/<branch>/<artifact_folder>/<artifact files>
s3://<bucket>/<repo>/<branch>/<artifact_folder>/manifest.json
s3://<bucket>/<repo>/branches.json
```

The repository folder is always derived from the caller repository name. The
branch folder is always derived from the current branch and URL-encoded so
branch names are reversible and do not collide. For example, `feature/foo`
becomes `feature%2Ffoo`.

Example caller:

```yaml
name: publish

on:
  workflow_dispatch:

permissions:
  contents: read
  actions: write
  id-token: write

jobs:
  publish:
    uses: sima-neat/.github/.github/workflows/vulcan-publish-artifacts.yml@main
    with:
      bucket: sima-neat-artifacts-dev
      role_to_assume: ${{ vars.ARTIFACT_PUBLISHER_ROLE_ARN }}
      environment_name: ${{ vars.VULCAN_ENV || 'dev' }}
      artifact_pattern: neat-build-*
      artifact_glob: "**/*"
      artifact_folder: artifacts
```

Common inputs:

| Input | Default | Description |
| --- | --- | --- |
| `bucket` | required | Vulcan artifact S3 bucket. |
| `role_to_assume` | required | AWS IAM role ARN for GitHub OIDC. |
| `aws_region` | `us-west-2` | AWS region for STS and S3. |
| `environment_name` | `dev` | GitHub environment used for variables and approvals. |
| `artifact_folder` | `artifacts` | Folder under repo/branch where files are uploaded. |
| `source_branch` | caller branch | Optional branch used for the artifact path; use the upstream branch for `workflow_run` callers. |
| `source_commit` | caller SHA | Optional commit SHA used for the artifact path; use the upstream SHA for `workflow_run` callers. |
| `artifact_pattern` | required | GitHub Actions artifact name pattern to download. |
| `artifact_glob` | `**/*` | File glob to publish from downloaded artifacts. |
| `min_artifact_count` | `1` | Minimum matching file count required. |
| `merge_multiple` | `true` | Merge matching GitHub artifacts before publishing. |
| `publish_manifest` | `true` | Upload `manifest.json` with file hashes and run metadata. |
| `install_awscli` | `false` | Install AWS CLI v2 from Amazon's Linux installer. Leave false when the runner already has `aws`. |
| `cleanup_github_artifacts` | `true` | Delete matching GitHub Actions artifacts after a successful S3 publish. |

The AWS role trust policy must restrict GitHub OIDC subjects to the intended
repositories, branches, and environments. The workflow is public, so AWS IAM is
the enforcement point for who can publish to each bucket/prefix.

The branch index is fetched with the workflow's `GITHUB_TOKEN`. Transient GitHub
API `5xx` responses are retried with exponential backoff. If those retries are
exhausted, the workflow uses an unauthenticated request only after verifying the
caller repository is public; it never uses that fallback for private repositories
or for authentication and authorization failures such as `401` or `403`.

`branches.json` is generated from the caller repository's current active
GitHub branches each time artifacts are published. It is stored at:

```text
s3://<bucket>/<repo>/branches.json
```

## Update Latest Artifacts

Use `vulcan-update-latest-artifacts.yml` when a repository wants to promote a
published artifact set by updating only `latest-tag.txt`.

The file is written to:

```text
s3://<bucket>/<repo>/<branch>/<artifact_folder>/latest-tag.txt
```

By default, the workflow first verifies that
`s3://<bucket>/<repo>/<branch>/<artifact_folder>/manifest.json` exists. This
prevents promoting a branch/folder that has not published artifacts yet.

Example caller:

```yaml
name: promote-latest

on:
  workflow_dispatch:
    inputs:
      latest_tag:
        description: Value to write into latest-tag.txt
        required: true
        type: string

permissions:
  contents: read
  id-token: write

jobs:
  latest:
    uses: sima-neat/.github/.github/workflows/vulcan-update-latest-artifacts.yml@main
    with:
      bucket: sima-neat-artifacts-dev
      role_to_assume: ${{ vars.ARTIFACT_PUBLISHER_ROLE_ARN }}
      environment_name: ${{ vars.VULCAN_ENV || 'dev' }}
      artifact_folder: artifacts
      latest_tag: ${{ inputs.latest_tag }}
```

If `latest_tag` is empty, the workflow writes the current short commit SHA.
Use `source_branch` and `source_commit` when the workflow is called from a
`workflow_run` that must publish the triggering run's branch and commit rather
than the downstream workflow's default-branch context.
