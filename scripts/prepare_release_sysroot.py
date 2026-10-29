#!/usr/bin/env python3
"""Provision a manifest-resolved release in a disposable SDK using its transaction.

Only temporary copies of the SDK entry points are adapted. The installed SDK,
image receipt, downloader validation, private APT cache, directory transaction,
package inventory and compiler/link probes remain authoritative.
"""
import argparse
import hashlib
import json
import shlex
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

REPOSITORY = "https://debian.neat.sima.ai/release"


def read_resolution(path, firmware, version):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError("Resolution receipt must be an object")
    target = re.fullmatch(r"(3\.[0-9]+\.[0-9]+)_master_B[1-9][0-9]*", firmware)
    if (not target or not re.fullmatch(re.escape(target[1]) + r"(?:[-+][0-9A-Za-z.+-]+)?", version)
            or value.get("channel") != "release"
            or value.get("elxr_version") != firmware or value.get("sysroot_version") != version
            or value.get("package") != "simaai-palette-modalix"):
        raise ValueError("Resolution receipt differs from the requested firmware/sysroot")
    digest = value.get("index_sha256", "")
    expected_url = f"{REPOSITORY}/dists/agate/non-free/binary-arm64/by-hash/SHA256/{digest}"
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or value.get("index_url") != expected_url:
        raise ValueError("Invalid release index evidence")
    packages = value.get("release_packages")
    if not isinstance(packages, dict) or set(packages) != {"simaai-palette-modalix", "simaai-base-files-modalix"}:
        raise ValueError("Resolution requires Palette and base-files identities")
    for name, item in packages.items():
        if (not isinstance(item, dict) or item.get("Version") != version
                or item.get("Architecture") not in ({"all", "arm64"} if name == "simaai-palette-modalix" else {"arm64"})
                or not re.fullmatch(r"[0-9a-f]{64}", item.get("SHA256", ""))
                or not str(item.get("Size", "")).isdigit()):
            raise ValueError("Invalid release package identity in resolution")
    return value


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Unsupported installed SDK interface; refresh the SDK image")
    return text.replace(old, new)


def adapt_updater(text, version):
    # The exact argument is checked before the installed update/activation path.
    old = '    if [[ "${args[0]}" == *~git* ]]; then'
    new = '''    if [[ "${args[0]}" == RESOLVED_RELEASE_VERSION ]]; then
      channel=release
      repository=https://debian.neat.sima.ai/release
      revision_pattern='^([0-9]+\\.[0-9]+\\.[0-9]+)([-+][0-9A-Za-z.+-]+)?$'
    elif [[ "${args[0]}" == *~git* ]]; then'''
    new = new.replace('RESOLVED_RELEASE_VERSION', shlex.quote(version))
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


def verify(root, downloads, resolution):
    inventory = root / "var/lib/sima-sdk/sysroot-packages.tsv"
    versions = [f[2] for line in inventory.read_text().splitlines()
                if len(f := line.split("\t")) >= 3 and f[0] == "simaai-palette-modalix"]
    if versions != [resolution["sysroot_version"]]:
        raise ValueError(f"Unexpected platform inventory: {versions}")
    identity = (root / "etc/packageinfo").read_text()
    if not re.search(r"^SIMA_PACKAGE_VERSION[ \t]*=[ \t]*" + re.escape(resolution["elxr_version"]) + r"[ \t]*$", identity, re.M):
        raise ValueError("Release packageinfo differs from the requested firmware")
    for name, expected in resolution["release_packages"].items():
        archives = list(downloads.glob(name + "*.deb"))
        if (len(archives) != 1 or archives[0].stat().st_size != int(expected["Size"])
                or hashlib.sha256(archives[0].read_bytes()).hexdigest() != expected["SHA256"]):
            raise ValueError(f"{name} archive differs from the resolution receipt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elxr-version", required=True)
    parser.add_argument("--sysroot-version", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    resolution = read_resolution(args.receipt, args.elxr_version, args.sysroot_version)
    if os.environ.get("SIMA_DISPOSABLE_SDK") != "1" or os.geteuid() != 0:
        raise ValueError("Release provisioning requires root in an explicitly disposable SDK")
    if os.environ.get("APT_CONFIG"):
        raise ValueError("External APT_CONFIG is not permitted")
    release = Path(os.environ.get("SDK_RELEASE_FILE", "/etc/sdk-release"))
    if not re.search(r"^Platform Base[ \t]*=[ \t]*" + re.escape(args.elxr_version.split("_", 1)[0]) + r"[ \t]*$", release.read_text(), re.M):
        raise ValueError("An existing Agate 3.0 SDK/toolchain is required")
    root = Path(os.environ.get("SYSROOT", "/opt/toolchain/aarch64/modalix"))
    updater = Path(shutil.which("sysroot") or "/nonexistent/sysroot")
    setup = Path(shutil.which("setup-sdk-sysroot.sh") or "/nonexistent/setup-sdk-sysroot.sh")
    downloader = Path("/opt/bin/simaai_setup_sdk.py")
    with tempfile.TemporaryDirectory(prefix="release-sdk-") as temporary:
        work = Path(temporary)
        adapted = work / "sysroot"
        adapted.write_text(adapt_updater(updater.read_text(), args.sysroot_version))
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
        subprocess.run([str(adapted), "update", args.sysroot_version], env=env, check=True)
        verify(root, downloads, resolution)
        subprocess.run([str(updater), "status"], check=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Release sysroot provisioning failed: {exc}")
