# SDK package consumer audit — 2026-10-27

Related: https://github.com/sima-neat/core/issues/1197

Audit scope: committed `develop` workflows and CI setup/build scripts in Core,
Runner, Scheduler, Codecs, Kernels and LLiMa. Host-bootstrap and DevKit package
operations are outside the SDK-container helper contract. Target sysroot tooling
owns its separate package database and must not be wrapped as SDK host setup.

| Consumer | Finding and adoption |
| --- | --- |
| Core | Unconditional SDK `dpkg --configure -a` after setup. Replace with the helper exported by pinned `vulcan-build.yml`. |
| Runner | Conditional Ninja bootstrap chains dpkg/APT; existing APT timeout excludes dpkg and update. Install pinned composite action; call helper per operation. |
| Scheduler | Same Ninja bootstrap; build also refreshes SDK host APT lists for yaml-cpp downloads. Use pinned action for bootstrap and host index refresh; retain private sysroot update/download/extraction logic. |
| Codecs | Conditional Ninja/Debian packaging tools bootstrap has unprotected APT update. Use pinned action and helper per operation. |
| Kernels | EV74 host compiler bootstrap chains dpkg/APT. Use helper from pinned reusable workflow; retain conditional Clang installation. A65/sysroot tools and DevKit setup are separate. |
| LLiMa | Unconditional dpkg recovery inside SDK build shell. Move to the explicit SDK container boundary through the helper from pinned reusable workflow. |

These changes do not alter SDK cache/image selection, dependency lists, firmware,
sysroot resolution, publication gates, public APIs or package ABI. Repositories
remain responsible for verifying container/image identity before package setup.

Additional local workflow survey found host APT bootstrap in Internals and SDK
jobs, and no matching direct SDK package bootstrap in Models or Model Compiler's
workflow/CI paths. These are not evidence of SDK-container lock contention and
are not migrated here. General installers and package-manager implementations
remain separate from this CI SDK startup fix.

Merge the shared PR first, then consumer PRs pinned to its exact implementation
commit. Run the affected package setup/build paths; source checks alone do not
qualify product builds, devices, or publication.
