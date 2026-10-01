import base64
import hashlib
import io
import json
import mimetypes
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
from typing import ClassVar
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
        "base64": base64,
        "datetime": datetime,
        "hashlib": hashlib,
        "json": json,
        "mimetypes": mimetypes,
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


def object_head(data):
    return {
        "ContentLength": len(data),
        "ETag": '"' + hashlib.md5(data).hexdigest() + '"',
        "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
    }


@pytest.fixture(autouse=True)
def forbid_live_aws(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Tests must not invoke real AWS commands")

    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.mark.parametrize("previous", [None, b"new bytes", b"old bytes"])
def test_exact_file_publication_and_rerun(tmp_path, previous):
    helpers = publisher_helpers()
    path = tmp_path / "package"
    path.write_bytes(b"new bytes")
    head = object_head(path.read_bytes())
    uploads = []
    helpers["s3_object"] = lambda *_: object_head(previous) if previous else None
    helpers["s3_json"] = lambda *_: head
    helpers["upload_checked"] = lambda *args: uploads.append(args)
    checksum, existed, identity = helpers["publish_file"](
        "bucket", "exact/key", path, True
    )
    assert checksum == hashlib.sha256(path.read_bytes()).hexdigest()
    assert existed == (previous is not None)
    assert identity == {"etag": head["ETag"], "size": head["ContentLength"]}
    assert len(uploads) == (0 if previous == path.read_bytes() else 1)


def test_cannot_replace_without_invalidation_permission(tmp_path):
    helpers = publisher_helpers()
    path = tmp_path / "package"
    path.write_bytes(b"new")
    helpers["s3_object"] = lambda *_: object_head(b"old")
    with pytest.raises(SystemExit, match="without CDN invalidation"):
        helpers["publish_file"]("bucket", "key", path, False)


def test_s3_verification_failure_blocks_publication(tmp_path):
    helpers = publisher_helpers()
    path = tmp_path / "package"
    path.write_bytes(b"new")
    helpers["s3_object"] = lambda *_: None
    helpers["upload_checked"] = lambda *_: None
    helpers["s3_json"] = lambda *_: object_head(b"bad")
    with pytest.raises(SystemExit, match="S3 content verification"):
        helpers["publish_file"]("bucket", "key", path, True)


@pytest.mark.parametrize("keys", [[], ["exact/key-sibling"], ["exact/key"]])
def test_exact_missing_key_only_and_access_failures(keys):
    helpers = publisher_helpers()
    calls = []

    def aws(operation, *args):
        calls.append((operation, args))
        return (
            {"Contents": [{"Key": key} for key in keys]}
            if operation == "list-objects-v2"
            else object_head(b"data")
        )

    helpers["s3_json"] = aws
    result = helpers["s3_object"]("bucket", "exact/key")
    assert (result is not None) == ("exact/key" in keys)
    assert calls[0][1][calls[0][1].index("--prefix") + 1] == "exact/key"
    assert len(calls) == (2 if result else 1)

    def denied(*args):
        raise subprocess.CalledProcessError(1, "aws", stderr="AccessDenied")

    helpers["s3_json"] = denied
    with pytest.raises(subprocess.CalledProcessError):
        helpers["s3_object"]("bucket", "key")


@pytest.mark.parametrize("previous", [None, {"ETag": "old"}])
def test_conditional_put_with_service_checksum(tmp_path, previous):
    helpers = publisher_helpers()
    path = tmp_path / "package"
    path.write_bytes(b"content")
    calls = []
    helpers["s3_json"] = lambda *args: calls.append(args) or {}
    helpers["upload_checked"](
        "bucket", "key", path, previous, hashlib.sha256(b"content").hexdigest()
    )
    args = calls[0]
    assert args[0] == "put-object"
    assert args[args.index("--server-side-encryption") + 1] == "aws:kms"
    assert "--sse" not in args
    flag = "--if-match" if previous else "--if-none-match"
    assert args[args.index(flag) + 1] == ("old" if previous else "*")
    assert (
        args[args.index("--checksum-sha256") + 1]
        == object_head(b"content")["ChecksumSHA256"]
    )


@pytest.mark.parametrize("fail_completion", [False, True])
def test_multipart_checksum_conditional_completion_and_abort(tmp_path, fail_completion):
    helpers = publisher_helpers()
    path = tmp_path / "large"
    path.write_bytes(b"abcdefghij")
    helpers["part_size"] = lambda *_: 4
    calls, parts = [], []

    def aws(operation, *args):
        calls.append((operation, args))
        if operation == "create-multipart-upload":
            return {"UploadId": "upload"}
        if operation == "upload-part":
            data = Path(args[args.index("--body") + 1]).read_bytes()
            parts.append(data)
            assert (
                args[args.index("--checksum-sha256") + 1]
                == object_head(data)["ChecksumSHA256"]
            )
            return {"ETag": "part"}
        if operation == "complete-multipart-upload":
            assert args[args.index("--if-match") + 1] == "old"
            manifest = json.loads(
                Path(args[args.index("--multipart-upload") + 1][7:]).read_text()
            )
            assert [p["PartNumber"] for p in manifest["Parts"]] == [1, 2, 3]
            if fail_completion:
                raise subprocess.CalledProcessError(1, "aws")
        return {}

    helpers["s3_json"] = aws
    helpers["run"] = lambda *args: calls.append(("abort", args))
    if fail_completion:
        with pytest.raises(subprocess.CalledProcessError):
            helpers["upload_checked"]("bucket", "key", path, {"ETag": "old"}, "unused")
        assert calls[-1][0] == "abort"
    else:
        helpers["upload_checked"]("bucket", "key", path, {"ETag": "old"}, "unused")
        assert calls[-1][0] == "complete-multipart-upload"
    assert b"".join(parts) == path.read_bytes()
    assert max(map(len, parts)) == 4


def test_legacy_manifest_keeps_first_publication_provenance(tmp_path):
    helpers = publisher_helpers()
    path = tmp_path / "manifest.json"
    stable = {"repository": "other/repo", "artifacts": [{"sha256": "abc"}]}
    original = (
        json.dumps(
            {**stable, "published_at_utc": "old", "run_id": "1", "run_attempt": "1"}
        )
        + "\n"
    )
    path.write_text(json.dumps(stable))
    helpers["s3_object"] = lambda *_: {"ETag": "old"}

    def aws(operation, *args):
        assert operation == "get-object"
        assert args[args.index("--if-match") + 1] == "old"
        Path(args[-1]).write_text(original)
        return {}

    helpers["s3_json"] = aws
    helpers["preserve_legacy_manifest"]("bucket", "key", path)
    assert path.read_text() == original


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
    expected = {"etag": '"etag"', "size": 7}

    class Response(io.BytesIO):
        headers: ClassVar[dict] = {"ETag": '"etag"', "Content-Length": "7"}

        def read(self, *args):
            pytest.fail("CDN verification must not download the body")

    def success(request, **kwargs):
        assert request.get_method() == "HEAD"
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", success)
    assert helpers["verify_cloudfront_file"]("https://cdn", "/key", expected)
    Response.headers = {"ETag": '"stale"', "Content-Length": "7"}
    assert not helpers["verify_cloudfront_file"]("https://cdn", "/key", expected)
    for code, headers, retry in [
        (404, {}, True),
        (403, {"Age": "5"}, True),
        (403, {}, False),
        (500, {}, False),
    ]:

        def error(*args, code=code, headers=headers, **kwargs):
            raise urllib.error.HTTPError("url", code, "error", headers, io.BytesIO())

        monkeypatch.setattr(urllib.request, "urlopen", error)
        if retry:
            assert not helpers["verify_cloudfront_file"](
                "https://cdn", "/key", expected
            )
        else:
            with pytest.raises(urllib.error.HTTPError):
                helpers["verify_cloudfront_file"]("https://cdn", "/key", expected)


def test_publication_barrier_and_no_prefix_invalidation():
    content = workflow()
    assert "s3_prefix_has_objects" not in content
    assert "prefix_existed" not in content
    assert "cancel-in-progress: false" in content
    assert "inputs.artifact_folder }}" in content
    barrier = content.index(
        "ensure_cloudfront_consistency(distribution_id, base_url, published_objects, replaced_paths)"
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
    helpers["s3_object"] = lambda bucket, key: objects.get(key)
    helpers["s3_json"] = lambda operation, *args: objects[args[args.index("--key") + 1]]

    def upload(bucket, key, path, previous, checksum):
        objects[key] = object_head(path.read_bytes())

    helpers["upload_checked"] = upload
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
        _checksum, existing, identity = helpers["publish_file"](
            "bucket", key, path, True
        )
        assert not existing
        helpers["ensure_cloudfront_consistency"](
            "dist", "https://cdn", {"/" + key: identity}, []
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
        calls.append(("publish", key))
        or (
            helpers["sha256_file"](path),
            True,
            {"etag": "tag", "size": path.stat().st_size},
        )
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


@pytest.mark.parametrize("folder", ["", ".", "./", " amd64/ubuntu ", "amd64-ubuntu"])
def test_lock_uses_normalized_destination(tmp_path, monkeypatch, folder):
    output = tmp_path / "outputs"
    env = {
        "ARTIFACT_BUCKET": "bucket",
        "ARTIFACT_NAMESPACE": " core ",
        "ARTIFACT_FOLDER_INPUT": folder,
        "SOURCE_BRANCH_INPUT": " main ",
        "SOURCE_COMMIT_INPUT": "a" * 40,
        "GITHUB_OUTPUT": str(output),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    code = re.search(
        r"python3 - <<'PYCODE'\n(.*?)^          PYCODE$",
        workflow(),
        re.MULTILINE | re.DOTALL,
    )
    assert code
    exec(textwrap.dedent(code[1]), {})  # noqa: S102 - execute repository-owned normalizer
    expected_folder = "" if folder in {"", ".", "./"} else "amd64-ubuntu"
    destination = ["bucket", "core", "main", "a" * 12, expected_folder]
    assert (
        output.read_text()
        == "lock=" + hashlib.sha256(json.dumps(destination).encode()).hexdigest() + "\n"
    )
    assert "group: vulcan-publish-${{ needs.prepare.outputs.lock }}" in workflow()


def test_multipart_head_checks_composite_without_download(tmp_path):
    helpers = publisher_helpers()
    size = 5 * 1024 * 1024
    data = b"a" * size + b"b" * 17
    path = tmp_path / "package"
    path.write_bytes(data)
    parts = [hashlib.sha256(data[:size]).digest(), hashlib.sha256(data[size:]).digest()]
    checksum = (
        base64.b64encode(hashlib.sha256(b"".join(parts)).digest()).decode() + "-2"
    )
    head = {
        "ContentLength": len(data),
        "ChecksumSHA256": checksum,
        "Metadata": {"vulcan-part-size": str(size)},
    }
    assert helpers["same_s3_content"](
        "bucket", "key", path, head, hashlib.sha256(data).hexdigest()
    )
    head["ChecksumSHA256"] = "wrong-2"
    assert not helpers["same_s3_content"](
        "bucket", "key", path, head, hashlib.sha256(data).hexdigest()
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_cross_owner_rerun_preserves_manifest(tmp_path, monkeypatch, legacy):
    helpers = publisher_helpers()
    monkeypatch.chdir(tmp_path)
    download = tmp_path / "download"
    download.mkdir()
    (download / "artifact").write_bytes(b"artifact")
    for key, value in {
        "GITHUB_REPOSITORY": "external/repo",
        "ARTIFACT_NAMESPACE": "repo",
        "GITHUB_REF_NAME": "main",
        "GITHUB_SHA": "a" * 40,
        "ARTIFACT_FOLDER_INPUT": "",
        "DOWNLOAD_PATH": str(download),
        "ARTIFACT_GLOB": "*",
        "MIN_ARTIFACT_COUNT": "1",
        "ARTIFACT_BUCKET": "bucket",
        "CLOUDFRONT_DISTRIBUTION_ID": "",
        "PUBLISH_MANIFEST": "true",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
        "GH_TOKEN": "fake",
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("SOURCE_BRANCH_INPUT", "SOURCE_COMMIT_INPUT", "GITHUB_HEAD_REF"):
        monkeypatch.delenv(key, raising=False)
    objects, puts = {}, []

    def aws(operation, *args):
        key = args[args.index("--key") + 1] if "--key" in args else None
        if operation == "list-objects-v2":
            prefix = args[args.index("--prefix") + 1]
            return {
                "Contents": [
                    {"Key": k} for k in sorted(objects) if k.startswith(prefix)
                ][:1]
            }
        if operation == "head-object":
            return object_head(objects[key])
        if operation == "get-object":
            Path(args[-1]).write_bytes(objects[key])
            return {}
        if operation == "put-object":
            assert "--if-none-match" in args
            assert key not in objects
            objects[key] = Path(args[args.index("--body") + 1]).read_bytes()
            puts.append(key)
            return {}
        pytest.fail(operation)

    helpers["s3_json"] = aws
    helpers["run"] = lambda *_: None
    helpers["summary"] = lambda *_: None
    helpers["fetch_branches"] = lambda *_: {"branches": []}
    code = re.search(
        r"^          github_repo =.*?(?=^          PY$)",
        workflow(),
        re.MULTILINE | re.DOTALL,
    )
    assert code
    exec(textwrap.dedent(code[0]), helpers)  # noqa: S102 - execute repository-owned publisher
    manifest_key = next(key for key in objects if key.endswith("manifest.json"))
    if legacy:
        doc = json.loads(objects[manifest_key])
        doc.update(run_id="1", run_attempt="1", published_at_utc="original")
        objects[manifest_key] = json.dumps(doc).encode()
    original = objects[manifest_key]
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    exec(textwrap.dedent(code[0]), helpers)  # noqa: S102 - execute repository-owned publisher
    assert len(puts) == 2  # No artifact or manifest upload on the rerun.
    assert objects[manifest_key] == original
    (download / "artifact").write_bytes(b"changed")
    with pytest.raises(SystemExit, match="without CDN invalidation"):
        exec(textwrap.dedent(code[0]), helpers)  # noqa: S102 - execute repository-owned publisher
