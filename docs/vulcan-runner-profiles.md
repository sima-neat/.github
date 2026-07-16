# Vulcan Runner Profiles

The shared `vulcan-build.yml` workflow supports multiple Vulcan runner
profiles while keeping the original SDK build behavior as the default.

## SDK Profile

Existing callers do not need to change. Omitting `runner_profile` keeps the
legacy SDK label set:

```yaml
jobs:
  build:
    uses: sima-neat/.github/.github/workflows/vulcan-build.yml@main
    with:
      vulcan_env: production
      sdk_cache: sdk-latest
      capacity: default
      ensure_sdk_container: true
```

This resolves to the existing runner labels:

```text
self-hosted, linux, arm64, vulcan-production, sdk-build, sdk-latest, default
```

## Generic Ubuntu Profile

Use `runner_profile: ubuntu` for large CI jobs that need Vulcan capacity but do
not need an SDK container or Model Compiler cache.

```yaml
jobs:
  build:
    uses: sima-neat/.github/.github/workflows/vulcan-build.yml@main
    with:
      vulcan_env: production
      runner_profile: ubuntu
      architecture: amd64
      capacity: expensive
      ensure_sdk_container: false
      build_command: ./build.sh
```

The profile emits labels such as:

```text
self-hosted, linux, amd64, vulcan-production,
vulcan-profile-ubuntu, vulcan-arch-amd64, vulcan-size-expensive, expensive
```

## Model Compiler Profile

Use `runner_profile: model-compiler` for jobs that need a Model Compiler cache
snapshot. Capacity uses the standard Vulcan tiers; choose `most-expensive` only
when the job needs the largest configured runner.

```yaml
jobs:
  build:
    uses: sima-neat/.github/.github/workflows/vulcan-build.yml@main
    with:
      vulcan_env: production
      runner_profile: model-compiler
      architecture: amd64
      capacity: expensive
      cache: model-compiler-2.1.2-amd64
      ensure_sdk_container: false
      build_command: llima-compile --help
```

The workflow adds the cached Model Compiler virtual environment to `PATH` when
the profile is `model-compiler`.
