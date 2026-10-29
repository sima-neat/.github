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
import subprocess
import tarfile
import tempfile
from urllib.parse import urlsplit
import time
from urllib.error import URLError
from urllib.request import urlopen

MIRROR = "https://debian.neat.sima.ai"
PACKAGE = "simaai-palette-modalix"
INDEX = "non-free/binary-arm64/Packages.gz"



def firmware_identity(value: str) -> tuple[str, str, str]:
    match = re.fullmatch(r"(3\.[0-9]+\.[0-9]+)_(master|develop)_B([1-9][0-9]*)", value)
    if not match:
        raise ValueError("elxr_version must be a Platform 3 master/develop firmware identity")
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
                data = response.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise ValueError("mirror response exceeds 64 MiB")
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


def package_firmware(archive: bytes) -> str:
    """Read only packageinfo from a checksum-verified DEB; never extract its paths."""
    with tempfile.TemporaryDirectory(prefix="sysroot-identity-") as temporary:
        path = Path(temporary) / "base-files.deb"
        path.write_bytes(archive)
        with subprocess.Popen(["dpkg-deb", "--fsys-tarfile", str(path)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            identities = []
            try:
                with tarfile.open(fileobj=process.stdout, mode="r|*") as contents:
                    for member in contents:
                        if member.name.removeprefix("./") != "etc/packageinfo":
                            continue
                        if not member.isfile() or member.size > 65536:
                            raise ValueError("invalid base-files packageinfo")
                        identities.extend(re.findall(
                            r"^SIMA_PACKAGE_VERSION[ \t]*=[ \t]*([^\s]+)[ \t]*$",
                            contents.extractfile(member).read().decode(), re.M))
                process.stdout.read()  # Drain tar padding before waiting for dpkg-deb.
                if process.wait() != 0:
                    raise ValueError("could not read base-files package")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    if len(identities) != 1:
        raise ValueError("base-files must identify exactly one firmware")
    firmware_identity(identities[0])
    return identities[0]


def read_index(channel, fetcher):
    root = f"{MIRROR}/{channel}/dists/agate"
    digest, size = index_reference(fetcher(f"{root}/Release").decode())
    url = f"{root}/non-free/binary-arm64/by-hash/SHA256/{digest}"
    compressed = fetcher(url)
    if len(compressed) != size or hashlib.sha256(compressed).hexdigest() != digest:
        raise ValueError("Packages.gz size or SHA256 does not match Release")
    records = []
    for record in re.split(r"\n\s*\n", gzip.decompress(compressed).decode()):
        records.append(dict(line.split(": ", 1) for line in record.splitlines()
                            if ": " in line and not line[:1].isspace()))
    return records, {"index_url": url, "index_sha256": digest}


def package_record(fields):
    """Retain the exact index identity, not a mutable inferred version/hash."""
    for key in ("Version", "Architecture", "Filename", "Size", "SHA256"):
        if not isinstance(fields.get(key), str) or not fields[key]:
            raise ValueError(f"missing release package field: {key}")
    filename = fields["Filename"]
    if (not filename.startswith("pool/") or urlsplit(filename).query
            or any(part in {"", ".", ".."} for part in filename.split("/"))
            or not re.fullmatch(r"[A-Za-z0-9_+~./%-]+", filename)):
        raise ValueError("invalid release package filename")
    if (not fields["Size"].isdigit() or int(fields["Size"]) > 64 * 1024 * 1024
            or not re.fullmatch(r"[0-9a-f]{64}", fields["SHA256"])):
        raise ValueError("invalid release package size or SHA256")
    return {key: fields[key] for key in ("Version", "Architecture", "Filename", "Size", "SHA256")}


def resolve(firmware: str, fetcher=fetch, identity_reader=package_firmware) -> dict:
    base, branch, build = firmware_identity(firmware)
    channel = "pre-release" if branch == "master" else "daily"
    records, evidence = read_index(channel, fetcher)
    pattern = (rf"{re.escape(base)}~pre{build}" if branch == "master" else
               rf"{re.escape(base)}~git[0-9]{{12}}\.[0-9a-f]{{7,40}}-{build}")
    versions = {item.get("Version") for item in records
                if item.get("Package") == PACKAGE and item.get("Architecture") in {"all", "arm64"}
                and re.fullmatch(pattern, item.get("Version", ""))}
    matches = [{"sysroot_version": version, "channel": channel, **evidence} for version in versions]
    if branch == "master":
        records, evidence = read_index("release", fetcher)
        # Release promotion removes the build suffix. Match the requested base,
        # then establish the exact build from the indexed base-files payload.
        palettes = [item for item in records if item.get("Package") == PACKAGE
                    and item.get("Architecture") in {"all", "arm64"}
                    and re.fullmatch(re.escape(base) + r"(?:[-+][0-9A-Za-z.+-]+)?", item.get("Version", ""))]
        for palette in palettes:
            base_files = [item for item in records
                          if item.get("Package") == "simaai-base-files-modalix"
                          and item.get("Architecture") == "arm64"
                          and item.get("Version") == palette["Version"]]
            if len(base_files) != 1:
                raise ValueError("release Palette requires exactly one matching base-files package")
            packages = {PACKAGE: package_record(palette),
                        "simaai-base-files-modalix": package_record(base_files[0])}
            identity = packages["simaai-base-files-modalix"]
            archive = fetcher(f"{MIRROR}/release/{identity['Filename']}")
            if len(archive) != int(identity["Size"]) or hashlib.sha256(archive).hexdigest() != identity["SHA256"]:
                raise ValueError("base-files size or SHA256 does not match the verified index")
            if identity_reader(archive) == firmware:
                matches.append({"sysroot_version": palette["Version"], "channel": "release",
                                "release_packages": packages, **evidence})
    if len(matches) != 1:
        raise ValueError(f"expected one {PACKAGE} version for {firmware}, found {len(matches)}")
    return {"elxr_version": firmware, "package": PACKAGE, **matches[0]}


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
