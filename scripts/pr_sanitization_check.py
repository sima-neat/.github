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
DEFAULT_ALLOWED_MAIN_SOURCES = ("develop", "release-*")

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


def validate(
    base_ref: str,
    body: str,
    allowed_patterns: tuple[str, ...],
    head_ref: str = "",
    head_repo: str = "",
    base_repo: str = "",
    allowed_main_source_patterns: tuple[str, ...] = DEFAULT_ALLOWED_MAIN_SOURCES,
) -> ValidationResult:
    messages: list[str] = []
    valid_main_promotion = False

    if not base_ref:
        messages.append("Unable to determine the pull request target branch.")
    elif base_ref == "main":
        allowed_source_text = ", ".join(allowed_main_source_patterns)
        if not head_ref:
            messages.append(
                "Unable to determine the pull request source branch. PRs targeting main must "
                f"come from {allowed_source_text} branches."
            )
        elif head_repo and base_repo and head_repo != base_repo:
            messages.append(
                f"This PR targets 'main' from fork repository '{head_repo}'. PRs targeting main "
                f"must come from {allowed_source_text} branches in '{base_repo}'."
            )
        elif _base_allowed(head_ref, allowed_main_source_patterns):
            valid_main_promotion = True
        else:
            messages.append(
                f"This PR targets 'main' from '{head_ref}', but PRs targeting main must come "
                f"from {allowed_source_text} branches."
            )
    elif not _base_allowed(base_ref, allowed_patterns):
        allowed_text = ", ".join(allowed_patterns)
        messages.append(
            f"This PR targets '{base_ref}', but PRs must target {allowed_text} branches. "
            "Please retarget the PR before review. Promotion to main should use the approved "
            "release or promotion flow."
        )

    if not valid_main_promotion and not _has_issue_reference(body):
        messages.append(
            "This PR description does not reference a GitHub issue. All work must start from an "
            "issue, and the PR body must link to that issue before review. Add a reference such as "
            "'Fixes #123', 'Refs #123', 'sima-neat/core#123', or a full GitHub issue URL."
        )

    return ValidationResult(ok=not messages, messages=tuple(messages))


def _resolve_pr_fields(args: argparse.Namespace) -> tuple[str, str, str, str, str]:
    base_ref = args.base_ref
    head_ref = args.head_ref
    base_repo = args.base_repo
    head_repo = args.head_repo
    body = args.body

    if args.event_path:
        event = _load_event(args.event_path)
        pr = event.get("pull_request") or {}
        base = pr.get("base") or {}
        head = pr.get("head") or {}
        if not base_ref:
            base_ref = (base.get("ref") or "").strip()
        if not head_ref:
            head_ref = (head.get("ref") or "").strip()
        if not base_repo:
            base_repo = (((base.get("repo") or {}).get("full_name")) or "").strip()
        if not head_repo:
            head_repo = (((head.get("repo") or {}).get("full_name")) or "").strip()
        if body is None:
            body = pr.get("body") or ""

    return (
        (base_ref or "").strip(),
        (head_ref or "").strip(),
        (base_repo or "").strip(),
        (head_repo or "").strip(),
        body or "",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PR target branch and issue references.")
    parser.add_argument("--event-path", default="", help="Path to GitHub event JSON.")
    parser.add_argument("--base-ref", default="", help="PR base branch override.")
    parser.add_argument("--head-ref", default="", help="PR source branch override.")
    parser.add_argument("--base-repo", default="", help="PR base repository full name override.")
    parser.add_argument("--head-repo", default="", help="PR source repository full name override.")
    parser.add_argument("--body", default=None, help="PR body override.")
    parser.add_argument(
        "--allowed-base-patterns",
        default=",".join(DEFAULT_ALLOWED_BASES),
        help="Comma-separated allowed PR base branch patterns.",
    )
    args = parser.parse_args()

    try:
        base_ref, head_ref, base_repo, head_repo, body = _resolve_pr_fields(args)
        allowed_patterns = _split_allowed_base_patterns(args.allowed_base_patterns)
        result = validate(
            base_ref,
            body,
            allowed_patterns,
            head_ref=head_ref,
            head_repo=head_repo,
            base_repo=base_repo,
        )
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
