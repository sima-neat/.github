import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/vulcan-publish-artifacts.yml"


def workflow():
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def publisher_helpers():
    match = re.search(
        r"^          def clean_path_part\(.*?(?=^          github_repo =)",
        workflow(),
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    namespace = {
        "datetime": datetime,
        "hashlib": hashlib,
        "json": json,
        "os": os,
        "Path": Path,
        "quote": quote,
        "re": re,
        "subprocess": subprocess,
        "tempfile": tempfile,
        "time": time,
        "timezone": timezone,
        "urllib": urllib,
    }
    exec(textwrap.dedent(match.group()), namespace)  # noqa: S102 - exercise repository-owned workflow code
    return namespace


def test_encoded_branch_viewer_path():
    helpers = publisher_helpers()
    branch = helpers["branch_key"]("feature/foo")
    assert branch == "feature%2Ffoo"
    assert (
        helpers["cloudfront_viewer_path"](f"core/{branch}/abc/file")
        == "/core/feature%252Ffoo/abc/file"
    )


@pytest.mark.parametrize("previous", [None, "identical", "old bytes"])
def test_exact_file_publication_and_rerun(tmp_path, previous):
    helpers = publisher_helpers()
    path = tmp_path / "package"
    path.write_bytes(b"new bytes")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    if previous == "identical":
        previous = expected
    checks = iter([previous, expected])
    keys, uploads = [], []
    helpers["s3_checksum"] = lambda bucket, key: (
        keys.append((bucket, key)) or next(checks)
    )
    helpers["run"] = lambda *args: uploads.append(args)
    key = "core/main/abc/arm64/package"
    assert helpers["publish_file"]("bucket", key, path, True) == (
        expected,
        previous is not None,
    )
    assert keys == [("bucket", key), ("bucket", key)]
    assert len(uploads) == (0 if previous == expected else 1)


def test_cannot_replace_without_invalidation_permission(tmp_path):
    helpers = publisher_helpers()
    path = tmp_path / "package"
    path.write_bytes(b"new bytes")
    helpers["s3_checksum"] = lambda *_: "old"
    helpers["run"] = lambda *_: pytest.fail("must not upload")
    with pytest.raises(SystemExit, match="without CDN invalidation"):
        helpers["publish_file"]("bucket", "key", path, False)


def test_s3_verification_failure_blocks_publication(tmp_path):
    helpers = publisher_helpers()
    path = tmp_path / "package"
    path.write_bytes(b"new bytes")
    checks = iter([None, "corrupted"])
    helpers["s3_checksum"] = lambda *_: next(checks)
    helpers["run"] = lambda *_: None
    with pytest.raises(SystemExit, match="S3 content verification"):
        helpers["publish_file"]("bucket", "key", path, True)


def test_exact_missing_key_only_and_access_failures(monkeypatch):
    helpers = publisher_helpers()
    calls = []

    def missing(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, stderr="(NoSuchKey)")

    monkeypatch.setattr(subprocess, "run", missing)
    assert helpers["s3_checksum"]("bucket", "core/abc/arm64/file") is None
    assert calls[0][1:3] == ["s3api", "get-object"]
    assert calls[0][calls[0].index("--key") + 1] == "core/abc/arm64/file"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args, 1, stderr="(AccessDenied)"
        ),
    )
    with pytest.raises(SystemExit, match="AccessDenied"):
        helpers["s3_checksum"]("bucket", "key")


def events(results, existing=()):
    helpers = publisher_helpers()
    calls = []
    results = iter(results)
    helpers["verify_cloudfront_file"] = lambda *args: (
        calls.append(("verify", args[1])) or next(results)
    )

    def invalidate(distribution, paths, reason):
        if paths:
            calls.append(("invalidate-and-wait", tuple(paths), reason))
        return len(paths)

    helpers["create_cloudfront_invalidation"] = invalidate
    helpers["summary"] = lambda text: calls.append(("summary", text))
    helpers["ensure_cloudfront_consistency"](
        "dist", "https://cdn", {"/key": "sha"}, list(existing)
    )
    return calls


def test_first_publish_zero_invalidations():
    result = events([True])
    assert result[0] == ("verify", "/key")
    assert "invalidation paths: 0" in result[-1][1]


def test_rerun_invalidates_exact_existing_path_before_verification():
    result = events([True], ["/key"])
    assert result[0][:2] == ("invalidate-and-wait", ("/key",))
    assert result[1] == ("verify", "/key")
    assert "invalidation paths: 1" in result[-1][1]


def test_negative_cache_fallback_and_failed_repair():
    result = events([False, True])
    assert result[0] == ("verify", "/key")
    assert result[1] == (
        "invalidate-and-wait",
        ("/key",),
        "stale/negative-cache fallback",
    )
    assert result[2] == ("verify", "/key")
    with pytest.raises(SystemExit, match="fallback invalidation"):
        events([False, False])
    with pytest.raises(SystemExit, match="replacement invalidation"):
        events([False], ["/key"])


def test_invalidation_waits_and_records_exact_unique_paths():
    helpers = publisher_helpers()
    calls, summaries = [], []
    helpers["run_output"] = lambda *args: calls.append(args) or "id"
    helpers["run"] = lambda *args: calls.append(args)
    helpers["summary"] = summaries.append
    assert (
        helpers["create_cloudfront_invalidation"](
            "dist", ["/arm64/file", "/amd64/file", "/arm64/file"], "rerun"
        )
        == 2
    )
    assert calls[0][calls[0].index("--paths") + 1 : calls[0].index("--query")] == (
        "/amd64/file",
        "/arm64/file",
    )
    assert calls[1][:4] == ("aws", "cloudfront", "wait", "invalidation-completed")
    assert "path count: 2" in summaries[0]
    assert not any("*" in arg for call in calls for arg in call)


def test_cdn_error_classification(monkeypatch):
    helpers = publisher_helpers()
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    checksum = hashlib.sha256(b"content").hexdigest()
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"content")
    )
    assert helpers["verify_cloudfront_file"]("https://cdn", "/key", checksum)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"stale")
    )
    assert not helpers["verify_cloudfront_file"]("https://cdn", "/key", checksum)

    def error(*args, **kwargs):
        raise urllib.error.HTTPError("url", 404, "missing", {}, io.BytesIO())

    monkeypatch.setattr(urllib.request, "urlopen", error)
    assert not helpers["verify_cloudfront_file"]("https://cdn", "/key", checksum)

    def denied(*args, **kwargs):
        raise urllib.error.HTTPError("url", 403, "denied", {}, io.BytesIO())

    monkeypatch.setattr(urllib.request, "urlopen", denied)
    with pytest.raises(urllib.error.HTTPError):
        helpers["verify_cloudfront_file"]("https://cdn", "/key", checksum)


def test_publication_barrier_and_no_prefix_invalidation():
    content = workflow()
    assert "s3_prefix_has_objects" not in content
    assert "prefix_existed" not in content
    assert "cancel-in-progress: false" in content
    assert "inputs.artifact_folder }}" in content
    barrier = content.index(
        "ensure_cloudfront_consistency(distribution_id, base_url, published_checksums, replaced_paths)"
    )
    assert barrier < content.index("branches = fetch_branches")
    helper = content[
        content.index("          def create_cloudfront_invalidation") : content.index(
            "          def is_mutable_index"
        )
    ]
    assert "/*" not in helper
    assert "latest.tag" not in helper
    assert "latest.json" not in helper
    assert "branches.json" not in helper


def test_parallel_sibling_folders_do_not_trigger_invalidation(tmp_path):
    helpers = publisher_helpers()
    path = tmp_path / "package"
    path.write_bytes(b"package")
    objects, invalidations = {}, []
    helpers["s3_checksum"] = lambda bucket, key: objects.get(key)

    def upload(*args):
        objects[args[4].removeprefix("s3://bucket/")] = helpers["sha256_file"](
            Path(args[3])
        )

    helpers["run"] = upload
    helpers["verify_cloudfront_file"] = lambda *_: True
    helpers["summary"] = lambda *_: None
    helpers["create_cloudfront_invalidation"] = lambda distribution, paths, reason: (
        invalidations.extend(paths) or len(paths)
    )
    for folder in (
        "ubuntu22/amd64",
        "ubuntu22/arm64",
        "ubuntu24/amd64",
        "ubuntu24/arm64",
    ):
        key = f"core/main/abc/pciehost/{folder}/package"
        checksum, existing = helpers["publish_file"]("bucket", key, path, True)
        assert not existing
        helpers["ensure_cloudfront_consistency"](
            "dist", "https://cdn", {"/" + key: checksum}, []
        )
    assert len(objects) == 4
    assert invalidations == []


@pytest.mark.parametrize("name", ["latest.tag", "latest.json", "branches.json"])
def test_caching_disabled_indexes_never_invalidate(name):
    helpers = publisher_helpers()
    invalidations = []
    helpers["create_cloudfront_invalidation"] = lambda distribution, paths, reason: (
        invalidations.extend(paths) or len(paths)
    )
    helpers["summary"] = lambda *_: None
    helpers["verify_cloudfront_file"] = lambda *_: True
    path = f"/core/main/{name}"
    helpers["ensure_cloudfront_consistency"](
        "dist", "https://cdn", {path: "sha"}, [path]
    )
    assert invalidations == []
    helpers["verify_cloudfront_file"] = lambda *_: False
    with pytest.raises(SystemExit, match="Caching-disabled"):
        helpers["ensure_cloudfront_consistency"](
            "dist", "https://cdn", {path: "sha"}, [path]
        )
    assert invalidations == []


@pytest.mark.parametrize("fail_verification", [False, True])
def test_embedded_publisher_barrier_including_manifest(
    tmp_path, monkeypatch, fail_verification
):
    helpers = publisher_helpers()
    monkeypatch.chdir(tmp_path)
    download = tmp_path / "download"
    download.mkdir()
    (download / "package.deb").write_bytes(b"package")
    for name, value in {
        "GITHUB_REPOSITORY": "sima-neat/core",
        "ARTIFACT_NAMESPACE": "core",
        "GITHUB_REF_NAME": "feature/foo",
        "GITHUB_SHA": "a" * 40,
        "ARTIFACT_FOLDER_INPUT": "arm64",
        "DOWNLOAD_PATH": str(download),
        "ARTIFACT_GLOB": "*",
        "MIN_ARTIFACT_COUNT": "1",
        "ARTIFACT_BUCKET": "bucket",
        "CLOUDFRONT_DISTRIBUTION_ID": "dist",
        "ARTIFACT_BASE_URL": "https://cdn",
        "PUBLISH_MANIFEST": "true",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "2",
        "GH_TOKEN": "test",
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("GITHUB_HEAD_REF", "SOURCE_BRANCH_INPUT", "SOURCE_COMMIT_INPUT"):
        monkeypatch.delenv(name, raising=False)
    calls = []
    helpers["publish_file"] = lambda bucket, key, path, allow: (
        calls.append(("publish", key)) or (helpers["sha256_file"](path), True)
    )
    helpers["fetch_branches"] = lambda *_: (
        calls.append(("fetch-index",)) or {"branches": []}
    )
    helpers["run"] = lambda *args: calls.append(("upload-index", args))

    def barrier(distribution, base, published, existing):
        calls.append(("barrier",))
        assert len(published) == 2
        assert set(existing) == set(published)
        assert all(path.startswith("/core/feature%252Ffoo/") for path in published)
        if fail_verification:
            raise SystemExit("verification failed")

    helpers["ensure_cloudfront_consistency"] = barrier
    match = re.search(
        r"^          github_repo =.*?(?=^          PY$)",
        workflow(),
        re.MULTILINE | re.DOTALL,
    )
    assert match
    if fail_verification:
        with pytest.raises(SystemExit, match="verification failed"):
            exec(textwrap.dedent(match.group()), helpers)  # noqa: S102 - exercise repository-owned workflow code
        assert [item[0] for item in calls] == ["publish", "publish", "barrier"]
    else:
        exec(textwrap.dedent(match.group()), helpers)  # noqa: S102 - exercise repository-owned workflow code
        assert [item[0] for item in calls] == [
            "publish",
            "publish",
            "barrier",
            "fetch-index",
            "upload-index",
        ]
