# SDK package consumer audit — 2026-10-27

Related: https://github.com/sima-neat/core/issues/1197

Audit scope: committed `develop` workflows and CI setup/build scripts in Core,
Runner, Scheduler, Codecs, Kernels and LLiMa. Host-bootstrap and DevKit package
operations are outside the SDK-container helper contract. Target sysroot tooling
owns its separate package database and must not be wrapped as SDK host setup.

| Consumer | Finding and adoption |
| --- | --- |
| Core | Unconditional SDK `dpkg --configure -a` after setup. Replace with the helper exported by `vulcan-build.yml@main`. |
| Runner | Conditional Ninja bootstrap chains dpkg/APT; existing APT timeout excludes dpkg and update. Install composite action at `@main`; call helper per operation. |
| Scheduler | Same Ninja bootstrap; build also refreshes SDK host APT lists for yaml-cpp downloads. Use action at `@main` for bootstrap; stage the same helper inside the SDK for host index refresh after sysroot preparation. Retain private sysroot update/download/extraction logic. |
| Codecs | Conditional Ninja/Debian packaging tools bootstrap has unprotected APT update. Use action at `@main` and helper per operation. |
| Kernels | EV74 host compiler bootstrap chains dpkg/APT. Use helper from reusable workflow at `@main`; retain conditional Clang installation. A65/sysroot tools and DevKit setup are separate. |
| LLiMa | Unconditional dpkg recovery inside SDK build shell. Move to the explicit SDK container boundary through the helper from reusable workflow at `@main`. |

These changes do not alter SDK cache/image selection, dependency lists, firmware,
sysroot resolution, publication gates, public APIs or package ABI. Repositories
remain responsible for verifying container/image identity before package setup.

Additional committed workflow survey found host APT bootstrap in Internals and
SDK jobs, DevKit Python-venv provisioning in Models, and no matching direct SDK
package bootstrap in Model Compiler's main workflow/CI paths. These are outside
this SDK-container startup fix. General installers and target-sysroot package
implementations retain their own transaction contracts.

## Audited baseline revisions

- Core: `develop` at `6c62a67dcd04ad73072aceb3ac8b516ceac2cdcf`
- Runner: `develop` at `8f55add7d4d6afe735b4920208aaa2cabf64e481`
- Scheduler: `develop` at `d71fc92047a73f2902c2f51b7abfa48e398f937b`
- Codecs: `develop` at `f0538838d6cb38709652eaa9781eaacd1b1350cf`
- Kernels: `develop` at `9ae6ec934da1b1c24c3698c4be320246694020ed`
- LLiMa: `develop` at `c63441b4ca0c74f1282bc350e77483975ec40559`
- Models: `develop` at `38193d44b0132a97235577374a8bfa7845633f84`
- SDK: `develop` at `3ee6807d626c724b79dd2b4ef52114bc044668ef`
- Internals: `develop` at `ce6b01ccdc94c871d765c1477edeb72d4b3f1c59`
- Model Compiler: `main` at `bfec0bfa8030ccedec9fdba6d729d1fcd3291fa8`

Merge the shared PR into `main` first, then consumer PRs referencing `@main`.
Each run resolves the helper from the shared revision selected for that run. Run the affected package setup/build paths; source checks alone do not
qualify product builds, devices, or publication.
