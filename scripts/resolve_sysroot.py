#!/usr/bin/env python3
"""Resolve a Modalix firmware identity to an exact mirrored APT platform receipt."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.error import URLError
from urllib.request import urlopen

MIRROR = "https://debian.neat.sima.ai"
PACKAGE = "simaai-palette-modalix"
INDEX = "non-free/binary-arm64/Packages.gz"

# Release promotion keeps the master firmware identity but drops ~preBUILD.
# The base-files payload's /etc/packageinfo identifies this exact firmware.
RELEASE_FIRMWARE = "3.0.0_master_B2479"
RELEASE_VERSION = "3.0.0"
RELEASE_PACKAGES = {
    "simaai-palette-modalix": "4d0f89d6187f333d22aa931928659c0aa99d203f1b3f7af32d828e8a572f1770",
    "simaai-base-files-modalix": "e53080e6003a621bbcdc37f01bd186669e31891ded2f9c32c17ac3e3d0a0a1b3",
}


def firmware_identity(value: str) -> tuple[str, str, str]:
    match = re.fullmatch(r"(3\.0\.0)_(master|develop)_B([1-9][0-9]*)", value)
    if not match:
        raise ValueError("elxr_version must be 3.0.0_master_B<build> or 3.0.0_develop_B<build>")
    return match.groups()


def manifest_selection(data: dict) -> tuple[str | None, str | None]:
    if not isinstance(data, dict):
        raise ValueError("dependency manifest must be a JSON object")
    firmware = data.get("elxr_version")
    legacy = data.get("sysroot-version")
    if firmware is not None:
        if not isinstance(firmware, str):
            raise ValueError("elxr_version must be a string")
        base, _, _ = firmware_identity(firmware)
        if data.get("platform-version") != base:
            raise ValueError("elxr_version and platform-version disagree")
        if legacy is not None:
            raise ValueError("remove deprecated sysroot-version when setting elxr_version")
        return firmware, None
    if isinstance(legacy, str) and re.fullmatch(r"3\.0\.0~(?:pre[1-9][0-9]*|git[0-9]{12}\.[0-9a-f]{7,40}-[1-9][0-9]*)", legacy):
        if data.get("platform-version") != "3.0.0":
            raise ValueError("sysroot-version and platform-version disagree")
        return None, legacy
    raise ValueError("manifest requires elxr_version (legacy exact sysroot-version is deprecated)")


def fetch(url: str) -> bytes:
    for attempt in range(3):
        try:
            with urlopen(url, timeout=30) as response:
                data = response.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise ValueError("mirror response exceeds 16 MiB")
            return data
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def index_reference(release: str) -> tuple[str, int]:
    in_sha256 = False
    entries = []
    for line in release.splitlines():
        if not line.startswith(" "):
            in_sha256 = line == "SHA256:"
        elif in_sha256:
            fields = line.split()
            if len(fields) == 3 and fields[2] == INDEX:
                digest, size, _ = fields
                if not re.fullmatch(r"[0-9a-f]{64}", digest) or not size.isdigit():
                    raise ValueError("invalid Packages.gz SHA256 reference")
                entries.append((digest, int(size)))
    if len(entries) != 1:
        raise ValueError("Release must contain exactly one Packages.gz SHA256 reference")
    return entries[0]


def resolve(firmware: str, fetcher=fetch) -> dict:
    base, branch, build = firmware_identity(firmware)
    channel = "release" if firmware == RELEASE_FIRMWARE else ("pre-release" if branch == "master" else "daily")
    root = f"{MIRROR}/{channel}/dists/agate"
    digest, size = index_reference(fetcher(f"{root}/Release").decode())
    index_url = f"{root}/non-free/binary-arm64/by-hash/SHA256/{digest}"
    compressed = fetcher(index_url)
    if len(compressed) != size or hashlib.sha256(compressed).hexdigest() != digest:
        raise ValueError("Packages.gz size or SHA256 does not match Release")
    text = gzip.decompress(compressed).decode()
    pattern = (rf"{re.escape(base)}~pre{build}" if branch == "master" else
               rf"{re.escape(base)}~git[0-9]{{12}}\.[0-9a-f]{{7,40}}-{build}")
    if firmware == RELEASE_FIRMWARE:
        pattern = re.escape(RELEASE_VERSION)
    matches = set()
    release_packages = {}
    for record in re.split(r"\n\s*\n", text):
        fields = dict(line.split(": ", 1) for line in record.splitlines()
                      if ": " in line and not line[:1].isspace())
        version = fields.get("Version", "")
        name = fields.get("Package")
        if firmware == RELEASE_FIRMWARE and name in RELEASE_PACKAGES and version == RELEASE_VERSION:
            architecture = "all" if name == PACKAGE else "arm64"
            if fields.get("Architecture") != architecture or fields.get("SHA256") != RELEASE_PACKAGES[name]:
                raise ValueError(f"release package identity changed for {name}; review the B2479 mapping")
            if name in release_packages:
                raise ValueError(f"duplicate release package: {name}")
            release_packages[name] = {key: fields[key] for key in ("Version", "Architecture", "SHA256")}
        if (fields.get("Package") == PACKAGE and fields.get("Architecture") in {"all", "arm64"}
                and re.fullmatch(pattern, version)):
            matches.add(version)
    if len(matches) != 1:
        raise ValueError(f"expected one {PACKAGE} version for {firmware}, found {len(matches)}")
    if firmware == RELEASE_FIRMWARE and set(release_packages) != set(RELEASE_PACKAGES):
        raise ValueError("B2479 requires the reviewed release Palette and base-files packages")
    return {"elxr_version": firmware, "sysroot_version": matches.pop(),
            **({"release_packages": release_packages} if release_packages else {}),
            "channel": channel, "package": PACKAGE, "index_url": index_url,
            "index_sha256": digest}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("deps/manifest.json"))
    parser.add_argument("--receipt", type=Path, default=Path("sysroot-resolution.json"))
    args = parser.parse_args()
    firmware, legacy = manifest_selection(json.loads(args.manifest.read_text()))
    if firmware:
        result = resolve(firmware)
    else:
        print("::warning::sysroot-version is deprecated; migrate to elxr_version")
        result = {"elxr_version": "", "sysroot_version": legacy, "source": "legacy-manifest"}
    args.receipt.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for key in ("elxr_version", "sysroot_version"):
                output.write(f"{key}={result[key]}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"Resolved `{firmware or 'legacy manifest'}` → `{result['sysroot_version']}`.\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(f"Sysroot resolution failed: {exc}")
