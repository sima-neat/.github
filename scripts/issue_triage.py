#!/usr/bin/env python3
"""Collect issue context and safely apply Codex triage proposals."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


API_BASE = "https://api.github.com"
TRIAGE_COMMENT_MARKER = "<!-- sima-neat-codex-issue-triage -->"
DEFAULT_MAX_COMMENT_CHARS = 2400
DEFAULT_TRIAGE_FILE_LIMIT_BYTES = 50_000
DEFAULT_TRIAGE_TOTAL_LIMIT_BYTES = 200_000
DEFAULT_TRIAGE_MAX_FILES = 20
DEFAULT_MAX_EXTENDED_REPOS = 2


def api_request(method: str, path: str, token: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"GitHub API {method} {path} failed: HTTP {exc.code} {body}") from exc


def api_paginated(path: str, token: str, max_pages: int = 20) -> list[Any]:
    separator = "&" if "?" in path else "?"
    base_path = f"{path}{separator}per_page=100"
    items: list[Any] = []
    for page in range(1, max_pages + 1):
        page_items = api_request("GET", f"{base_path}&page={page}", token)
        if not isinstance(page_items, list):
            raise SystemExit(f"GitHub API GET {base_path}&page={page} did not return a list")
        items.extend(page_items)
        if len(page_items) < 100:
            break
    return items


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return data


def safe_child_path(root: Path, child: str) -> Path:
    child_path = Path(child)
    if child_path.is_absolute() or ".." in child_path.parts:
        raise SystemExit(f"Unsafe path outside caller repository: {child!r}")
    root_resolved = root.resolve()
    resolved = (root_resolved / child_path).resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise SystemExit(f"Unsafe path outside caller repository: {child!r}")
    return resolved


def resolve_repo_triage_path(value: str) -> Path:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
    caller_root = workspace / "caller-repo"
    path = Path(value)
    if path.parts and path.parts[0] == "caller-repo":
        return safe_child_path(workspace, value)
    return safe_child_path(caller_root, value)


def config_int(config: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(max(value, minimum), maximum)


def read_repo_triage_files(
    repo_path: Path,
    limit_bytes: int = DEFAULT_TRIAGE_FILE_LIMIT_BYTES,
    max_files: int = DEFAULT_TRIAGE_MAX_FILES,
    total_limit_bytes: int = DEFAULT_TRIAGE_TOTAL_LIMIT_BYTES,
) -> list[dict[str, str]]:
    if not repo_path.exists() or not repo_path.is_dir():
        return []

    files: list[dict[str, str]] = []
    total_bytes = 0
    for path in sorted(repo_path.rglob("*")):
        if len(files) >= max_files or total_bytes >= total_limit_bytes:
            break
        if not path.is_file():
            continue
        if path.name == "config.json":
            continue
        if path.suffix.lower() not in {".md", ".txt", ".json", ".toml", ".yaml", ".yml"}:
            continue
        rel = path.relative_to(repo_path).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text.encode("utf-8")) > limit_bytes:
            text = text[:limit_bytes] + "\n\n[truncated]\n"
        remaining = total_limit_bytes - total_bytes
        encoded_len = len(text.encode("utf-8"))
        if encoded_len > remaining:
            text = text[:remaining] + "\n\n[truncated]\n"
            encoded_len = len(text.encode("utf-8"))
        files.append({"path": rel, "content": text})
        total_bytes += encoded_len
    return files


def collect_context(args: argparse.Namespace) -> None:
    token = require_env("GITHUB_TOKEN")
    repo = require_env("GITHUB_REPOSITORY")
    owner, name = repo.split("/", 1)
    issue_number = int(args.issue_number)
    triage_path = resolve_repo_triage_path(args.repo_triage_path)

    issue = api_request("GET", f"/repos/{owner}/{name}/issues/{issue_number}", token)
    all_comments = api_paginated(f"/repos/{owner}/{name}/issues/{issue_number}/comments", token)
    comments = all_comments[-100:]
    labels = api_paginated(f"/repos/{owner}/{name}/labels", token)
    config = read_json(triage_path / "config.json")
    triage_files = read_repo_triage_files(
        triage_path,
        limit_bytes=config_int(config, "triage_file_limit_bytes", DEFAULT_TRIAGE_FILE_LIMIT_BYTES, 1_000, 100_000),
        max_files=config_int(config, "triage_max_files", DEFAULT_TRIAGE_MAX_FILES, 0, 50),
        total_limit_bytes=config_int(
            config,
            "triage_total_limit_bytes",
            DEFAULT_TRIAGE_TOTAL_LIMIT_BYTES,
            10_000,
            500_000,
        ),
    )

    context = {
        "repository": repo,
        "issue_number": issue_number,
        "issue": {
            "title": issue.get("title"),
            "body": issue.get("body"),
            "state": issue.get("state"),
            "author": (issue.get("user") or {}).get("login"),
            "labels": [label.get("name") for label in issue.get("labels", [])],
            "created_at": issue.get("created_at"),
            "updated_at": issue.get("updated_at"),
            "url": issue.get("html_url"),
        },
        "comments": [
            {
                "author": (comment.get("user") or {}).get("login"),
                "body": comment.get("body"),
                "created_at": comment.get("created_at"),
            }
            for comment in comments
        ],
        "comments_included": len(comments),
        "comments_total_fetched": len(all_comments),
        "comments_order": "oldest_to_newest_latest_100",
        "available_labels": [label.get("name") for label in labels],
        "repo_triage_config": config,
        "repo_triage_files": triage_files,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote issue context to {output}")


def load_proposal(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise SystemExit("Triage proposal must be a JSON object")
    return data


def string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SystemExit(f"Proposal field {field} must be a list of strings")
    return value


def sanitize_path(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return safe or "repo"


def cross_reference_config(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    repos = config.get("cross_reference_repos", [])
    if not isinstance(repos, list):
        raise SystemExit("cross_reference_repos must be a list when provided")

    by_repo: dict[str, dict[str, str]] = {}
    for item in repos:
        if not isinstance(item, dict):
            raise SystemExit("cross_reference_repos entries must be JSON objects")
        repository = item.get("repository")
        if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise SystemExit(f"Invalid cross-reference repository: {repository!r}")
        ref = item.get("ref", "main")
        if not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", ref):
            raise SystemExit(f"Invalid ref for {repository}: {ref!r}")
        path = item.get("path", sanitize_path(repository))
        if not isinstance(path, str) or path.startswith("/") or ".." in Path(path).parts:
            raise SystemExit(f"Invalid path for {repository}: {path!r}")
        by_repo[repository] = {"repository": repository, "ref": ref, "path": path}
    return by_repo


def clone_repo(repository: str, ref: str, destination: Path, token: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    auth = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as git_config:
        git_config.write("[http \"https://github.com/\"]\n")
        git_config.write(f"\textraheader = AUTHORIZATION: basic {auth}\n")
        git_config_path = git_config.name
    os.chmod(git_config_path, 0o600)
    env["GIT_CONFIG_GLOBAL"] = git_config_path
    try:
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                ref,
                f"https://github.com/{repository}.git",
                str(destination),
            ],
            check=True,
            env=env,
        )
    finally:
        Path(git_config_path).unlink(missing_ok=True)


def prepare_extended_analysis(args: argparse.Namespace) -> None:
    token = require_env("GITHUB_TOKEN")
    triage_path = resolve_repo_triage_path(args.repo_triage_path)
    config = read_json(triage_path / "config.json")
    proposal = load_proposal(Path(args.proposal))
    allowed = cross_reference_config(config)
    max_extended_repos = config_int(config, "max_extended_repos", DEFAULT_MAX_EXTENDED_REPOS, 0, 5)
    extended_required = proposal.get("extended_analysis_required") is True
    requested = string_list(proposal.get("extended_analysis_repos"), "extended_analysis_repos") if extended_required else []
    requested = list(dict.fromkeys(requested))
    disallowed = sorted(set(requested) - set(allowed))
    allowed_requested = [repo for repo in requested if repo in allowed]
    selected = allowed_requested[:max_extended_repos]
    skipped_due_to_limit = allowed_requested[max_extended_repos:]

    output_dir = Path(args.output_dir)
    cloned: list[dict[str, str]] = []
    for repo in selected:
        spec = allowed[repo]
        destination = output_dir / spec["path"]
        clone_repo(spec["repository"], spec["ref"], destination, token)
        cloned.append({
            "repository": spec["repository"],
            "ref": spec["ref"],
            "path": str(destination),
        })

    summary = {
        "requested": requested,
        "cloned": cloned,
        "disallowed": disallowed,
        "skipped_due_to_limit": skipped_due_to_limit,
        "max_extended_repos": max_extended_repos,
        "run_extended_analysis": bool(cloned),
    }
    print(json.dumps(summary, indent=2))

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as output:
            output.write(f"run_extended_analysis={'true' if cloned else 'false'}\n")
            output.write(f"summary={json.dumps(summary, separators=(',', ':'))}\n")


def summarize_proposal(args: argparse.Namespace) -> None:
    proposal = load_proposal(Path(args.proposal))
    labels = string_list(proposal.get("labels"), "labels")
    comment = proposal.get("public_comment") or proposal.get("comment") or ""
    if not isinstance(comment, str):
        comment = ""
    summary = {
        "summary": proposal.get("summary"),
        "category": proposal.get("category"),
        "area": proposal.get("area"),
        "confidence": proposal.get("confidence"),
        "labels": labels,
        "extended_analysis_required": proposal.get("extended_analysis_required") is True,
        "extended_analysis_repos": string_list(proposal.get("extended_analysis_repos"), "extended_analysis_repos"),
        "needs_human_review": proposal.get("needs_human_review") is True,
        "public_comment_chars": len(comment),
    }
    print(json.dumps(summary, indent=2))


def format_triage_comment(comment: str) -> str:
    return f"{TRIAGE_COMMENT_MARKER}\n{comment}"


def find_existing_triage_comment(comments: list[Any]) -> dict[str, Any] | None:
    for comment in reversed(comments):
        body = comment.get("body") if isinstance(comment, dict) else None
        if isinstance(body, str) and TRIAGE_COMMENT_MARKER in body:
            return comment
    return None


def apply_proposal(args: argparse.Namespace) -> None:
    token = require_env("GITHUB_TOKEN")
    repo = require_env("GITHUB_REPOSITORY")
    owner, name = repo.split("/", 1)
    issue_number = int(args.issue_number)
    dry_run = args.dry_run.lower() == "true"

    triage_path = resolve_repo_triage_path(args.repo_triage_path)
    config = read_json(triage_path / "config.json")
    proposal = load_proposal(Path(args.proposal))

    automation = config.get("automation", {})
    if not isinstance(automation, dict):
        automation = {}
    apply_labels = bool(automation.get("apply_labels", True))
    post_comment = bool(automation.get("post_comment", True))
    max_comment_chars = config_int(config, "max_comment_chars", DEFAULT_MAX_COMMENT_CHARS, 400, 5000)

    labels_cfg = config.get("labels", {})
    allowed_labels = set()
    if isinstance(labels_cfg, dict):
        allowed_labels = {label for label in labels_cfg.get("allowed", []) if isinstance(label, str)}

    labels = string_list(proposal.get("labels"), "labels")
    if allowed_labels:
        labels_to_apply = [label for label in labels if label in allowed_labels]
        disallowed = sorted(set(labels) - allowed_labels)
    else:
        labels_to_apply = []
        disallowed = labels
    comment = proposal.get("public_comment") or proposal.get("comment") or ""
    if comment is None:
        comment = ""
    if not isinstance(comment, str):
        raise SystemExit("Proposal field public_comment must be a string")
    if len(comment) > max_comment_chars:
        comment = comment[: max_comment_chars - 20].rstrip() + "\n\n[truncated]"

    summary = {
        "dry_run": dry_run,
        "labels_requested": labels,
        "labels_to_apply": labels_to_apply,
        "labels_skipped": disallowed,
        "post_comment": bool(comment and post_comment),
        "comment_mode": "update-or-create",
        "comment_chars": len(comment),
    }
    print(json.dumps(summary, indent=2))

    if dry_run:
        return

    if apply_labels and labels_to_apply:
        api_request("POST", f"/repos/{owner}/{name}/issues/{issue_number}/labels", token, {"labels": labels_to_apply})

    if post_comment and comment:
        body = format_triage_comment(comment)
        comments = api_paginated(f"/repos/{owner}/{name}/issues/{issue_number}/comments", token)
        existing = find_existing_triage_comment(comments)
        if existing:
            api_request("PATCH", f"/repos/{owner}/{name}/issues/comments/{existing['id']}", token, {"body": body})
        else:
            api_request("POST", f"/repos/{owner}/{name}/issues/{issue_number}/comments", token, {"body": body})


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect-context")
    collect.add_argument("--issue-number", required=True)
    collect.add_argument("--repo-triage-path", required=True)
    collect.add_argument("--output", required=True)
    collect.set_defaults(func=collect_context)

    prepare = subparsers.add_parser("prepare-extended-analysis")
    prepare.add_argument("--repo-triage-path", required=True)
    prepare.add_argument("--proposal", required=True)
    prepare.add_argument("--output-dir", required=True)
    prepare.set_defaults(func=prepare_extended_analysis)

    summarize = subparsers.add_parser("summarize-proposal")
    summarize.add_argument("--proposal", required=True)
    summarize.set_defaults(func=summarize_proposal)

    apply_cmd = subparsers.add_parser("apply-proposal")
    apply_cmd.add_argument("--issue-number", required=True)
    apply_cmd.add_argument("--repo-triage-path", required=True)
    apply_cmd.add_argument("--proposal", required=True)
    apply_cmd.add_argument("--dry-run", required=True)
    apply_cmd.set_defaults(func=apply_proposal)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
