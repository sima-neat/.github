import hashlib
import json
import os
import re
import subprocess
import textwrap
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "vulcan-publish-artifacts.yml"


def workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def publisher_helpers() -> dict:
    content = workflow()
    match = re.search(
        r"^          def clean_path_part\(.*?(?=^          github_repo =)",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "Unable to locate embedded artifact publisher helpers"
    namespace = {
        "datetime": datetime,
        "hashlib": hashlib,
        "json": json,
        "os": os,
        "Path": Path,
        "quote": quote,
        "re": re,
        "subprocess": subprocess,
        "time": time,
        "timezone": timezone,
        "urllib": urllib,
    }
    exec(textwrap.dedent(match.group()), namespace)
    return namespace


def test_encoded_branch_s3_key_is_encoded_again_for_cloudfront_viewer_path() -> None:
    helpers = publisher_helpers()

    branch = helpers["branch_key"]("integration/yolox-seg-pose-detessdequant")
    assert branch == "integration%2Fyolox-seg-pose-detessdequant"

    viewer_path = helpers["cloudfront_viewer_path"](f"core/{branch}/abc123")
    assert viewer_path == "/core/integration%252Fyolox-seg-pose-detessdequant/abc123"


def test_s3_prefix_check_happens_at_the_commit_scope() -> None:
    helpers = publisher_helpers()
    calls = []
    helpers["run_output"] = lambda *args: calls.append(args) or "1"

    assert helpers["s3_prefix_has_objects"]("artifact-bucket", "core/develop/abc123")
    assert calls == [(
        "aws", "s3api", "list-objects-v2",
        "--bucket", "artifact-bucket",
        "--prefix", "core/develop/abc123/",
        "--max-keys", "1",
        "--query", "KeyCount",
        "--output", "text",
    )]


def cloudfront_events(verification_results: list[bool], prefix_existed: bool) -> list[str]:
    helpers = publisher_helpers()
    events = []
    results = iter(verification_results)
    helpers["verify_cloudfront_file"] = (
        lambda *_args: events.append("verify") or next(results)
    )
    helpers["create_cloudfront_invalidation"] = (
        lambda _distribution, viewer_prefix: events.append(f"invalidate:{viewer_prefix}/*")
    )

    helpers["ensure_cloudfront_consistency"](
        "DIST123",
        "https://artifacts.example.com",
        "/core/feature%252Ffoo/abc123",
        {"/core/feature%252Ffoo/abc123/metadata-all.json": "expected-sha256"},
        prefix_existed,
    )
    return events


def test_fresh_prefix_verifies_without_invalidation() -> None:
    assert cloudfront_events([True], prefix_existed=False) == ["verify"]


def test_existing_prefix_invalidates_exact_commit_prefix_then_verifies() -> None:
    assert cloudfront_events([True], prefix_existed=True) == [
        "invalidate:/core/feature%252Ffoo/abc123/*",
        "verify",
    ]


def test_fresh_prefix_verification_failure_triggers_narrow_fallback() -> None:
    assert cloudfront_events([False, True], prefix_existed=False) == [
        "verify",
        "invalidate:/core/feature%252Ffoo/abc123/*",
        "verify",
    ]


def test_verification_must_succeed_after_fallback_invalidation() -> None:
    with pytest.raises(SystemExit, match="fallback invalidation"):
        cloudfront_events([False, False], prefix_existed=False)


def test_mutable_indexes_are_not_invalidation_paths() -> None:
    content = workflow()
    invalidation_helper = re.search(
        r"^          def create_cloudfront_invalidation\(.*?(?=^          def ensure_cloudfront_consistency)",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert invalidation_helper is not None
    assert "latest.tag" not in invalidation_helper.group()
    assert "branches.json" not in invalidation_helper.group()
    assert '"--paths", f"{viewer_prefix.rstrip(\'/\')}/*"' in invalidation_helper.group()


def test_publish_waits_for_required_invalidation_and_verifies_content() -> None:
    content = workflow()

    assert '"--query", "Invalidation.Id"' in content
    assert '"aws", "cloudfront", "wait", "invalidation-completed"' in content
    assert "verify_cloudfront_file(" in content
    assert "for key, checksum in sorted(published_checksums.items())" in content


def test_metadata_success_does_not_hide_unavailable_package() -> None:
    helpers = publisher_helpers()
    events = []
    outcomes = iter([True, False, True])
    helpers['verify_cloudfront_file'] = lambda _base, path, _sha: events.append(path) or next(outcomes)
    helpers['create_cloudfront_invalidation'] = lambda *_args: events.append('invalidate')
    helpers['ensure_cloudfront_consistency'](
        'DIST123', 'https://artifacts.example.com', '/core/main/abc',
        {'/metadata.json': 'metadata-sha', '/package.deb': 'package-sha'}, False,
    )
    assert events == ['/metadata.json', '/package.deb', 'invalidate', '/package.deb']


@pytest.mark.parametrize('failure', ['503', 'checksum'])
def test_file_verification_retries_transient_errors_and_corrupt_content(monkeypatch, failure) -> None:
    import io

    helpers = publisher_helpers()
    calls = []
    payload = b'package contents'

    def open_url(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            if failure == '503':
                raise urllib.error.HTTPError(url, 503, 'Unavailable', {}, None)
            return io.BytesIO(b'corrupt content')
        return io.BytesIO(payload)

    monkeypatch.setattr(urllib.request, 'urlopen', open_url)
    monkeypatch.setattr(time, 'sleep', lambda _delay: None)
    assert helpers['verify_cloudfront_file'](
        'https://artifacts.example.com', '/package.deb', hashlib.sha256(payload).hexdigest(),
    )
    assert len(calls) == 2


def test_persistent_package_failure_blocks_publication() -> None:
    helpers = publisher_helpers()
    helpers['verify_cloudfront_file'] = lambda *_args: False
    helpers['create_cloudfront_invalidation'] = lambda *_args: None
    with pytest.raises(SystemExit, match='existing commit prefix'):
        helpers['ensure_cloudfront_consistency'](
            'DIST123', 'https://artifacts.example.com', '/core/main/abc',
            {'/package.deb': 'sha'}, True,
        )


@pytest.mark.parametrize('selectable', [False, True])
def test_metadata_references_must_be_uploaded_with_matching_checksums(tmp_path, selectable) -> None:
    helpers = publisher_helpers()
    metadata_path = tmp_path / 'metadata-extras.json'
    metadata = ({'selectable-resources': [{'resource': 'extras.tar.gz', 'checksum': 'expected'}]}
                if selectable else
                {'resources': ['extras.tar.gz'], 'resources-checksum': {'extras.tar.gz': 'expected'}})
    metadata_path.write_text(json.dumps(metadata))
    validate = helpers['validate_metadata_resources']
    with pytest.raises(SystemExit, match='unpublished resource'):
        validate(tmp_path, [metadata_path], {})
    with pytest.raises(SystemExit, match='checksum mismatch'):
        validate(tmp_path, [metadata_path], {'extras.tar.gz': 'wrong'})
    validate(tmp_path, [metadata_path], {'extras.tar.gz': 'expected'})
