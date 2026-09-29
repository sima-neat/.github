#!/usr/bin/env python3
"""Validate a caller artifact and hand it to Vulcan's single-writer publisher.

The AWS role used here may write only immutable intake objects and start/read a
channel-specific state machine. It must never read an archive signing key or
write the served APT bucket.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


PACKAGE_RE = re.compile(r"[a-z0-9][a-z0-9+.-]+\Z")
VERSION_RE = re.compile(r"[0-9][a-zA-Z0-9.+:~\-]*\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
SUITES = {"bookworm", "agate"}
ARCHES = {"amd64", "arm64", "all"}
CHANNELS = {"develop", "official"}


def command(*args: str, input_text: str | None = None) -> str:
    return subprocess.run(args, input=input_text, text=True, capture_output=True, check=True).stdout


def aws(*args: str) -> dict:
    return json.loads(command("aws", *args, "--output", "json"))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def https_url(value: str) -> str:
    parsed = urlparse(value)
    require(parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password,
            "public URLs must be HTTPS without credentials")
    require(not parsed.query and not parsed.fragment, "public URLs cannot have query or fragment")
    return value.rstrip("/")


def branch_token(ref: str) -> str:
    branch = ref.removeprefix("refs/heads/")
    slug = re.sub(r"[^a-z0-9]+", "-", branch.lower()).strip("-")[:32].strip("-") or "branch"
    # The suffix makes branches that normalize to the same slug distinct.
    return f"{slug}-{hashlib.sha256(branch.encode()).hexdigest()[:12]}"


def control_fields(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in command("dpkg-deb", "--field", str(path)).splitlines():
        if line.startswith((" ", "\t")):
            continue
        key, sep, value = line.partition(":")
        require(bool(sep) and key.lower() not in {name.lower() for name in fields},
                "invalid or duplicate Debian control field")
        fields[key] = value.strip()
    for key in ("Package", "Version", "Architecture", "Maintainer", "Description"):
        require(bool(fields.get(key)), f"package is missing {key}")
    require(not {name.lower() for name in fields}.intersection({"filename", "size", "sha256", "sha1", "md5sum"}),
            "package control contains repository-generated fields")
    # dpkg-deb --contents reads the payload, rather than trusting the ar header alone.
    subprocess.run(["dpkg-deb", "--contents", str(path)], stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE, check=True)
    return fields


def validate() -> tuple[Path, dict]:
    env = os.environ
    channel, suite, arch = (env[name] for name in ("CHANNEL", "SUITE", "ARCHITECTURE"))
    require(channel in CHANNELS, "unsupported channel")
    require(suite in SUITES, "unsupported suite")
    require(arch in ARCHES, "unsupported architecture")
    repo = env["SOURCE_REPOSITORY"]
    ref = env["SOURCE_REF"]
    commit = env["SOURCE_COMMIT"]
    require(repo == env.get("GITHUB_REPOSITORY") and repo.startswith("sima-neat/"),
            "source repository does not match the authenticated caller")
    require(ref == env.get("GITHUB_REF") and commit == env.get("GITHUB_SHA"),
            "source ref/commit does not match the authenticated caller")
    require(COMMIT_RE.fullmatch(commit) is not None, "invalid source commit")
    require(env.get("GITHUB_EVENT_NAME") in {"push", "workflow_dispatch", "release"},
            "publishing is limited to trusted push, release, or manual runs")
    sequence = env["BUILD_SEQUENCE"]
    require(sequence == f"{env.get('GITHUB_RUN_ID')}.{env.get('GITHUB_RUN_ATTEMPT')}",
            "build sequence must identify this run and attempt")
    lifecycle = env["LIFECYCLE_CLASS"]
    token = ""
    if channel == "develop":
        require(ref.startswith("refs/heads/") and len(ref) > len("refs/heads/"),
                "develop requires a branch ref")
        require(lifecycle in {"branch", "release-candidate"}, "invalid develop lifecycle")
        token = branch_token(ref)
    else:
        require(ref.startswith("refs/tags/v") and lifecycle == "official",
                "official requires a v-prefixed release tag and official lifecycle")
    name = env["PACKAGE_NAME"]
    version = env["PACKAGE_VERSION"]
    require(PACKAGE_RE.fullmatch(name) is not None, "invalid package name")
    require(VERSION_RE.fullmatch(version) is not None, "invalid package version")
    if channel == "official":
        require(ref == f"refs/tags/v{version}", "official tag must match package version")
    else:
        require("~dev." in version and token in version and sequence in version and commit[:12] in version,
                "develop version must contain branch token, run sequence, and commit prefix")
    expected = env["EXPECTED_SHA256"].lower()
    require(SHA_RE.fullmatch(expected) is not None, "expected SHA256 must be 64 hex digits")
    provenance = json.loads(env["BUILD_PROVENANCE"])
    require(isinstance(provenance, dict) and bool(provenance), "build provenance must be a nonempty JSON object")
    directory = Path(env["PACKAGE_DIRECTORY"])
    packages = list(directory.rglob("*.deb"))
    require(len(packages) == 1 and len([p for p in directory.rglob("*") if p.is_file()]) == 1,
            "artifact must contain exactly one .deb and no other files")
    package = packages[0]
    require(not package.is_symlink() and package.resolve().is_relative_to(directory.resolve()),
            "package artifact must be a regular file within its directory")
    require(digest(package) == expected, "package SHA256 differs from build output")
    fields = control_fields(package)
    require(fields["Package"] == name and fields["Version"] == version,
            "package control name/version does not match submission")
    require(fields["Architecture"] == arch, "package control architecture does not match submission")
    fingerprint = env["ARCHIVE_KEY_FINGERPRINT"].upper()
    require(re.fullmatch(r"[0-9A-F]{40}", fingerprint) is not None,
            "archive key fingerprint must contain 40 hex digits")
    channel_url = https_url(env["CHANNEL_URL"])
    require(urlparse(channel_url).path.endswith("/" + channel), "channel URL path does not match channel")
    key_url = https_url(env["ARCHIVE_KEY_URL"])
    require(urlparse(key_url).netloc == urlparse(channel_url).netloc,
            "archive key and channel must use the same host")
    submission_id = hashlib.sha256(
        f"{repo}\0{channel}\0{sequence}\0{expected}".encode()
    ).hexdigest()[:32]
    record = {
        "schema_version": 1,
        "submission_id": submission_id,
        "channel": channel,
        "suite": suite,
        "architecture": arch,
        "package_name": name,
        "package_version": version,
        "sha256": expected,
        "source_repository": repo,
        "source_ref": ref,
        "source_commit": commit,
        "build_sequence": sequence,
        "build_provenance": provenance,
        "lifecycle_class": lifecycle,
        "branch_token": token,
        "github_run_url": f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{env['GITHUB_RUN_ID']}",
    }
    return package, record


def download(url: str, destination: Path) -> None:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "vulcan-debian-publish"}), timeout=30) as response:
        require(response.geturl().startswith("https://"), "public endpoint redirected away from HTTPS")
        with destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)


def verify_public(record: dict, result: dict, package: Path) -> None:
    channel_url = https_url(os.environ["CHANNEL_URL"])
    require(result.get("channel_url") == channel_url, "publisher returned a different channel URL")
    require(result.get("sha256") == record["sha256"], "publisher returned a different package digest")
    for key in ("suite", "architecture", "package_name", "package_version", "source_commit", "branch_token"):
        require(result.get(key) == record[key], f"publisher result differs in {key}")
    package_url = https_url(result.get("package_url", ""))
    require(package_url.startswith(channel_url + "/pool/"), "publisher returned a package URL outside channel pool")
    with tempfile.TemporaryDirectory(prefix="vulcan-apt-verify-") as scratch:
        root = Path(scratch)
        key = root / "archive.asc"
        keyring = root / "archive.gpg"
        inrelease = root / "InRelease"
        release = root / "Release"
        public_package = root / "package.deb"
        download(os.environ["ARCHIVE_KEY_URL"], key)
        key_listing = command("gpg", "--batch", "--with-colons", "--show-keys", str(key)).upper()
        primary_keys = re.findall(r"^PUB:", key_listing, re.MULTILINE)
        fingerprints = re.findall(r"^FPR:::::::::([0-9A-F]+):$", key_listing, re.MULTILINE)
        require(len(primary_keys) == 1 and fingerprints and
                fingerprints[0] == os.environ["ARCHIVE_KEY_FINGERPRINT"].upper(),
                "public archive key fingerprint mismatch")
        keyring.write_bytes(subprocess.run(["gpg", "--batch", "--dearmor"], input=key.read_bytes(),
                                           capture_output=True, check=True).stdout)
        download(f"{channel_url}/dists/{record['suite']}/InRelease", inrelease)
        command("gpgv", "--keyring", str(keyring), "--output", str(release), str(inrelease))
        release_text = release.read_text()
        require(f"Suite: {record['suite']}" in release_text, "signed Release suite mismatch")
        require(f"Label: SiMa.ai {record['channel']}" in release_text, "signed Release label mismatch")
        expiry = re.search(r"^Valid-Until: (.+)$", release_text, re.MULTILINE)
        require(expiry is not None, "signed Release lacks Valid-Until")
        expiry_time = dt.datetime.strptime(expiry.group(1), "%a, %d %b %Y %H:%M:%S UTC").replace(tzinfo=dt.timezone.utc)
        require(expiry_time > dt.datetime.now(dt.timezone.utc), "signed Release has expired")
        download(package_url, public_package)
        require(digest(public_package) == digest(package), "public .deb differs from submitted artifact")
        expected_filename = urlparse(package_url).path.removeprefix(urlparse(channel_url).path + "/")
        require(expected_filename.startswith("pool/"), "package URL is not a channel-relative pool path")
        arches = ("amd64", "arm64") if record["architecture"] == "all" else (record["architecture"],)
        for arch in arches:
            index_rel = f"main/binary-{arch}/Packages"
            index_metadata = None
            in_sha256 = False
            for line in release_text.splitlines():
                if line == "SHA256:":
                    in_sha256 = True
                    continue
                if in_sha256 and line and not line.startswith(" "):
                    in_sha256 = False
                if not in_sha256:
                    continue
                parts = line.split()
                if len(parts) == 3 and parts[2] == index_rel and SHA_RE.fullmatch(parts[0]):
                    index_metadata = parts
            require(index_metadata is not None, f"signed Release does not index {arch}")
            index = root / f"Packages-{arch}"
            download(f"{channel_url}/dists/{record['suite']}/{index_rel}", index)
            require(digest(index) == index_metadata[0] and index.stat().st_size == int(index_metadata[1]),
                    "public Packages index does not match signed Release")
            expected = {
                "Package": record["package_name"],
                "Version": record["package_version"],
                "Architecture": record["architecture"],
                "SHA256": record["sha256"],
                "Filename": expected_filename,
                "Size": str(package.stat().st_size),
            }
            require(any(all(f"{key}: {value}" in stanza.splitlines() for key, value in expected.items())
                        for stanza in index.read_text().split("\n\n")),
                    f"published package is missing or misdirected in signed {arch} Packages index")


def submit(package: Path, record: dict) -> dict:
    bucket = os.environ["INTAKE_BUCKET"]
    state_machine = os.environ["INTAKE_STATE_MACHINE_ARN"]
    require(re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", bucket) is not None, "invalid intake bucket")
    require(state_machine.startswith("arn:aws:states:") and ":stateMachine:" in state_machine,
            "invalid publisher state machine ARN")
    repo_key = record["source_repository"]
    prefix = f"submissions/{repo_key}/{record['channel']}/{record['submission_id']}"
    package_key = f"{prefix}/package.deb"
    manifest_key = f"{prefix}/manifest.json"
    with tempfile.TemporaryDirectory(prefix="vulcan-apt-submit-") as scratch:
        manifest = Path(scratch) / "manifest.json"
        manifest.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        for path, key, content_type in ((package, package_key, "application/vnd.debian.binary-package"),
                                        (manifest, manifest_key, "application/json")):
            # If-None-Match makes each upload immutable even when a run is retried.
            aws("s3api", "put-object", "--bucket", bucket, "--key", key,
                "--body", str(path), "--content-type", content_type,
                "--if-none-match", "*")
        execution_input = {
            "schema_version": 1,
            "manifest_bucket": bucket,
            "manifest_key": manifest_key,
            "package_key": package_key,
            "submission_id": record["submission_id"],
        }
        response = aws("stepfunctions", "start-execution", "--state-machine-arn", state_machine,
                       "--name", record["submission_id"], "--input", json.dumps(execution_input))
        execution = response["executionArn"]
        deadline = time.monotonic() + 30 * 60
        while time.monotonic() < deadline:
            state = aws("stepfunctions", "describe-execution", "--execution-arn", execution)
            if state["status"] == "SUCCEEDED":
                result = json.loads(state["output"])
                verify_deadline = min(deadline, time.monotonic() + 3 * 60)
                while True:
                    try:
                        verify_public(record, result, package)
                        return result
                    except (ValueError, urllib.error.URLError) as error:
                        if time.monotonic() >= verify_deadline:
                            raise RuntimeError(f"public APT verification did not converge: {error}") from error
                        time.sleep(10)
            if state["status"] in {"FAILED", "TIMED_OUT", "ABORTED"}:
                raise RuntimeError(f"Vulcan publisher {state['status']}: {state.get('error', '')} {state.get('cause', '')}")
            time.sleep(15)
    raise TimeoutError(f"Vulcan publisher did not finish within 30 minutes: {execution}")


def main() -> None:
    package, record = validate()
    if os.environ["MODE"] == "validate":
        print(f"Validated {record['package_name']} {record['package_version']} ({record['sha256']})")
        return
    require(os.environ["MODE"] == "submit", "unsupported action mode")
    result = submit(package, record)
    outputs = {
        "published_version": record["package_version"],
        "package_url": result["package_url"],
        "package_sha256": record["sha256"],
        "channel_url": result["channel_url"],
        "branch_token": record["branch_token"],
    }
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as target:
        for key, value in outputs.items():
            target.write(f"{key}={value}\n")
    print(f"Verified signed publication: {record['package_name']} {record['package_version']} {result['package_url']}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, RuntimeError, TimeoutError, subprocess.CalledProcessError,
            urllib.error.URLError, json.JSONDecodeError) as error:
        print(f"Debian publication failed: {error}", file=sys.stderr)
        raise SystemExit(1)
