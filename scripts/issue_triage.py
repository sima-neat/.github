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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


API_BASE = "https://api.github.com"
TRIAGE_COMMENT_MARKER = "<!-- sima-neat-codex-issue-triage -->"


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


def read_repo_triage_files(repo_path: Path, limit_bytes: int = 100_000) -> list[dict[str, str]]:
    if not repo_path.exists() or not repo_path.is_dir():
        return []

    files: list[dict[str, str]] = []
    for path in sorted(repo_path.rglob("*")):
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
        files.append({"path": rel, "content": text})
    return files


def collect_context(args: argparse.Namespace) -> None:
    token = require_env("GITHUB_TOKEN")
    repo = require_env("GITHUB_REPOSITORY")
    owner, name = repo.split("/", 1)
    issue_number = int(args.issue_number)
    triage_path = Path(args.repo_triage_path)

    issue = api_request("GET", f"/repos/{owner}/{name}/issues/{issue_number}", token)
    all_comments = api_paginated(f"/repos/{owner}/{name}/issues/{issue_number}/comments", token)
    comments = all_comments[-100:]
    labels = api_paginated(f"/repos/{owner}/{name}/labels", token)
    config = read_json(triage_path / "config.json")
    triage_files = read_repo_triage_files(triage_path)

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
    subprocess.run(
        [
            "git",
            "-c",
            f"http.https://github.com/.extraheader=AUTHORIZATION: basic {auth}",
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


def prepare_extended_analysis(args: argparse.Namespace) -> None:
    token = require_env("GITHUB_TOKEN")
    config = read_json(Path(args.repo_triage_path) / "config.json")
    proposal = load_proposal(Path(args.proposal))
    allowed = cross_reference_config(config)
    extended_required = proposal.get("extended_analysis_required") is True
    requested = string_list(proposal.get("extended_analysis_repos"), "extended_analysis_repos") if extended_required else []
    requested = list(dict.fromkeys(requested))
    disallowed = sorted(set(requested) - set(allowed))
    selected = [repo for repo in requested if repo in allowed]

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
        "run_extended_analysis": bool(cloned),
    }
    print(json.dumps(summary, indent=2))

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as output:
            output.write(f"run_extended_analysis={'true' if cloned else 'false'}\n")
            output.write(f"summary={json.dumps(summary, separators=(',', ':'))}\n")


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

    config = read_json(Path(args.repo_triage_path) / "config.json")
    proposal = load_proposal(Path(args.proposal))

    automation = config.get("automation", {})
    if not isinstance(automation, dict):
        automation = {}
    apply_labels = bool(automation.get("apply_labels", True))
    post_comment = bool(automation.get("post_comment", True))
    max_comment_chars = int(config.get("max_comment_chars", 1200))

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
        "comment": comment,
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
