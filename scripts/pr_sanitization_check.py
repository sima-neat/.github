#!/usr/bin/env python3
"""Validate pull request target branch and issue references."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ALLOWED_BASES = ("develop", "release-*", "integration/*")

ISSUE_PATTERNS = (
    re.compile(
        r"(?im)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|references?|related\s+to)\s+"
        r"(?:https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/\d+|"
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+|#\d+)\b"
    ),
    re.compile(r"(?i)\bhttps://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/\d+\b"),
    re.compile(r"(?i)(?<![\w.-])[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+\b"),
)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    messages: tuple[str, ...]


def _load_event(path: str) -> dict:
    if not path:
        return {}
    event_path = Path(path)
    if not event_path.exists():
        raise FileNotFoundError(f"GitHub event file not found: {event_path}")
    with event_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _split_allowed_base_patterns(value: str) -> tuple[str, ...]:
    patterns = []
    for raw in value.split(","):
        pattern = raw.strip()
        if pattern:
            patterns.append(pattern)
    return tuple(patterns or DEFAULT_ALLOWED_BASES)


def _glob_match(value: str, pattern: str) -> bool:
    regex = re.escape(pattern).replace(r"\*", ".*")
    return re.fullmatch(regex, value) is not None


def _base_allowed(base_ref: str, allowed_patterns: tuple[str, ...]) -> bool:
    return any(_glob_match(base_ref, pattern) for pattern in allowed_patterns)


def _has_issue_reference(body: str) -> bool:
    return any(pattern.search(body or "") for pattern in ISSUE_PATTERNS)


def validate(base_ref: str, body: str, allowed_patterns: tuple[str, ...]) -> ValidationResult:
    messages: list[str] = []

    if not base_ref:
        messages.append("Unable to determine the pull request target branch.")
    elif not _base_allowed(base_ref, allowed_patterns):
        allowed_text = ", ".join(allowed_patterns)
        messages.append(
            f"This PR targets '{base_ref}', but PRs must target {allowed_text} branches. "
            "Please retarget the PR before review. Promotion to main should use the approved "
            "release or promotion flow."
        )

    if not _has_issue_reference(body):
        messages.append(
            "This PR description does not reference a GitHub issue. All work must start from an "
            "issue, and the PR body must link to that issue before review. Add a reference such as "
            "'Fixes #123', 'Refs #123', 'sima-neat/core#123', or a full GitHub issue URL."
        )

    return ValidationResult(ok=not messages, messages=tuple(messages))


def _resolve_pr_fields(args: argparse.Namespace) -> tuple[str, str]:
    base_ref = args.base_ref
    body = args.body

    if args.event_path:
        event = _load_event(args.event_path)
        pr = event.get("pull_request") or {}
        if not base_ref:
            base_ref = ((pr.get("base") or {}).get("ref") or "").strip()
        if body is None:
            body = pr.get("body") or ""

    return (base_ref or "").strip(), body or ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PR target branch and issue references.")
    parser.add_argument("--event-path", default="", help="Path to GitHub event JSON.")
    parser.add_argument("--base-ref", default="", help="PR base branch override.")
    parser.add_argument("--body", default=None, help="PR body override.")
    parser.add_argument(
        "--allowed-base-patterns",
        default=",".join(DEFAULT_ALLOWED_BASES),
        help="Comma-separated allowed PR base branch patterns.",
    )
    args = parser.parse_args()

    try:
        base_ref, body = _resolve_pr_fields(args)
        allowed_patterns = _split_allowed_base_patterns(args.allowed_base_patterns)
        result = validate(base_ref, body, allowed_patterns)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    if result.ok:
        print(f"PR sanitization passed for target branch '{base_ref}'.")
        return 0

    for message in result.messages:
        print(f"::error::{message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
