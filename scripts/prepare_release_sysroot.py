#!/usr/bin/env python3
"""Provision B2479 in an explicitly disposable SDK using its installed transaction.

Only temporary copies of the SDK entry points are adapted. The installed SDK,
image receipt, downloader validation, private APT cache, directory transaction,
package inventory and compiler/link probes remain authoritative.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

FIRMWARE = "3.0.0_master_B2479"
VERSION = "3.0.0"
REPOSITORY = "https://debian.neat.sima.ai/release"
BASE_FILES_SHA256 = "e53080e6003a621bbcdc37f01bd186669e31891ded2f9c32c17ac3e3d0a0a1b3"


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Unsupported installed SDK interface; refresh the SDK image")
    return text.replace(old, new)


def adapt_updater(text):
    # The exact argument is checked before the installed update/activation path.
    old = '    if [[ "${args[0]}" == *~git* ]]; then'
    new = '''    if [[ "${args[0]}" == 3.0.0 ]]; then
      channel=release
      repository=https://debian.neat.sima.ai/release
      revision_pattern='^([0-9]+\\.[0-9]+\\.[0-9]+)$'
    elif [[ "${args[0]}" == *~git* ]]; then'''
    for interface in ("configure_agate_update_apt", "validate_staged_sysroot", '"${DIRECTORY_HELPER}" activate'):
        if interface not in text:
            raise ValueError("Installed SDK lacks transactional Agate provisioning")
    return replace_once(text, old, new)


def adapt_downloader(text):
    # The installed updater isolates APT to release + Debian; this does not add
    # daily/pre-release to the stable candidate set. The mirror serves the same
    # reviewed release bytes when repo.sima.ai is unavailable to the runner.
    text = replace_once(text,
        'if stable and any(origin.site == "repo.sima.ai" and origin.codename == "agate"',
        'if stable and any(origin.site in {"repo.sima.ai", "debian.neat.sima.ai"} and origin.codename == "agate"')
    # Release components such as TVM legitimately retain upstream ~git versions.
    return replace_once(text, '            if "~" not in candidate.version:\n                daily.append(candidate)',
                        '            daily.append(candidate)')


def verify(root, downloads):
    inventory = root / "var/lib/sima-sdk/sysroot-packages.tsv"
    versions = [f[2] for line in inventory.read_text().splitlines()
                if len(f := line.split("\t")) >= 3 and f[0] == "simaai-palette-modalix"]
    if versions != [VERSION]:
        raise ValueError(f"Unexpected platform inventory: {versions}")
    identity = (root / "etc/packageinfo").read_text()
    if not re.search(r"^SIMA_PACKAGE_VERSION\s*=\s*" + re.escape(FIRMWARE) + r"\s*$", identity, re.M):
        raise ValueError("Release packageinfo does not identify B2479")
    archives = list(downloads.glob("simaai-base-files-modalix*.deb"))
    if len(archives) != 1 or hashlib.sha256(archives[0].read_bytes()).hexdigest() != BASE_FILES_SHA256:
        raise ValueError("B2479 base-files archive differs from the reviewed release")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elxr-version", required=True)
    parser.add_argument("--sysroot-version", required=True)
    args = parser.parse_args()
    if (args.elxr_version, args.sysroot_version) != (FIRMWARE, VERSION):
        raise ValueError("Release provisioning supports only 3.0.0_master_B2479 -> 3.0.0")
    if os.environ.get("SIMA_DISPOSABLE_SDK") != "1" or os.geteuid() != 0:
        raise ValueError("Release provisioning requires root in an explicitly disposable SDK")
    if os.environ.get("APT_CONFIG"):
        raise ValueError("External APT_CONFIG is not permitted")
    release = Path(os.environ.get("SDK_RELEASE_FILE", "/etc/sdk-release"))
    if not re.search(r"^Platform Base\s*=\s*3\.0\.0$", release.read_text(), re.M):
        raise ValueError("An existing Agate 3.0 SDK/toolchain is required")
    root = Path(os.environ.get("SYSROOT", "/opt/toolchain/aarch64/modalix"))
    updater = Path(shutil.which("sysroot") or "/nonexistent/sysroot")
    setup = Path(shutil.which("setup-sdk-sysroot.sh") or "/nonexistent/setup-sdk-sysroot.sh")
    downloader = Path("/opt/bin/simaai_setup_sdk.py")
    with tempfile.TemporaryDirectory(prefix="b2479-sdk-") as temporary:
        work = Path(temporary)
        adapted = work / "sysroot"
        adapted.write_text(adapt_updater(updater.read_text()))
        adapted.chmod(0o755)
        python = work / "simaai_setup_sdk.py"
        python.write_text(adapt_downloader(downloader.read_text()))
        platform_setup = work / "setup-sdk-sysroot.sh"
        platform_setup.write_text(replace_once(setup.read_text(), str(downloader), str(python)))
        platform_setup.chmod(0o755)
        # Callers retain these DEBs for provider ownership checks and receipts.
        downloads = Path(os.environ.get("SYSROOT_UPDATE_DOWNLOAD_DIR", str(work / "debs")))
        env = dict(os.environ, SYSROOT_PLATFORM_SETUP=str(platform_setup),
                   SYSROOT_UPDATE_DOWNLOAD_DIR=str(downloads), SDK_APT_CHANNEL="release")
        subprocess.run([str(adapted), "update", VERSION], env=env, check=True)
        verify(root, downloads)
        subprocess.run([str(updater), "status"], check=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"B2479 sysroot provisioning failed: {exc}")
