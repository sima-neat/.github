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
        "/core/feature%252Ffoo/abc123/metadata-all.json",
        "expected-sha256",
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
    assert '("metadata-all.json", "metadata.json", "manifest.json")' in content
